"""Scenario executor shared by ``dryrun`` and ``record``.

``run_steps`` performs each step's actions, then polls the expectations until
the step's timeout, taking a screenshot per step and recording wait windows
(expectation polls longer than 1.5 s) for the editor.  Every wait goes
through ``page.wait_for_timeout`` so Playwright keeps dispatching events
(screencast frames, console messages) while we wait.
"""
from __future__ import annotations

import base64
import datetime as _dt
import json
import logging
import os
import re
import time
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_STEP_TIMEOUT_S = 60.0
WAIT_WINDOW_MIN_S = 1.5
POLL_MS = 100
MOUSE_STEPS = 25
LOGIN_TIMEOUT_MS = 30_000
TAIL_S = 2.0
RESULT_KEYS = ("id", "title", "status", "expected", "observed", "screenshot", "seconds", "error")


class DriveError(RuntimeError):
    """Pipeline-level failure (Chrome/capture), not a feature failure. One-line message."""


# --------------------------------------------------------------------------- helpers
def _one_line(exc: BaseException) -> str:
    text = str(exc).strip()
    if not text:
        return exc.__class__.__name__
    first = text.splitlines()[0].strip()
    return first[:300]


def _paths(out: Path):
    try:
        from demo_smoke.env import Paths  # lazy: another builder's module

        return Paths(Path(out))
    except ImportError:
        pass  # env.py not present: create the same layout ourselves

    class _P:
        pass

    p = _P()
    for name in ("raw", "audio", "clips", "final", "logs"):
        d = Path(out) / name
        d.mkdir(parents=True, exist_ok=True)
        setattr(p, name, d)
    return p


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(name)).strip("_") or "shot"


def _resolve_url(app_url: str, target: str) -> str:
    target = str(target)
    if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.IGNORECASE):
        return target
    base = (app_url or "").rstrip("/")
    if target.startswith(("/", "?", "#")):
        return base + target
    return base + "/" + target


def _resolve_file(scenario: dict, name: str) -> str:
    p = Path(str(name)).expanduser()
    if not p.is_absolute():
        base = scenario.get("_dir")
        if base:
            p = Path(base) / p
    if not p.exists():
        raise FileNotFoundError(f"upload file not found: {p}")
    return str(p)


def _selector(arg) -> str:
    if isinstance(arg, str):
        return arg
    if isinstance(arg, dict) and "selector" in arg:
        return str(arg["selector"])
    raise ValueError(f"action needs a selector, got {arg!r}")


def _local_clock():
    t0 = time.monotonic()
    return lambda: time.monotonic() - t0


def _body_text(page) -> str:
    return page.evaluate("() => (document.body ? document.body.innerText : '') || ''")


def _wait_until(page, clock, target: float) -> None:
    """Block until ``clock() >= target`` while pumping Playwright events."""
    while True:
        remaining = target - clock()
        if remaining <= 0:
            return
        page.wait_for_timeout(min(100.0, max(1.0, remaining * 1000.0)))


class _Collector:
    """Console errors + failed responses observed on a page."""

    def __init__(self, page):
        self.page = page
        self.console_errors: list[str] = []
        self.failed_requests: list[dict] = []

    def _on_console(self, msg) -> None:
        try:
            if msg.type == "error":
                self.console_errors.append(msg.text)
        except Exception:
            log.debug("ignored error", exc_info=True)

    def _on_pageerror(self, err) -> None:
        self.console_errors.append(f"Uncaught {_one_line(err) if isinstance(err, BaseException) else str(err).splitlines()[0]}")

    def _on_response(self, resp) -> None:
        # No round-trips here: a sync API call inside an event handler never returns
        # (the reply is queued behind this very handler). Bodies are read in since().
        try:
            if resp.status >= 400:
                self.failed_requests.append({"url": resp.url, "status": resp.status, "body_excerpt": None, "_resp": resp})
        except Exception:
            log.debug("response handler failed", exc_info=True)

    def attach(self) -> None:
        self.page.on("console", self._on_console)
        self.page.on("pageerror", self._on_pageerror)
        self.page.on("response", self._on_response)

    def detach(self) -> None:
        for name, fn in (("console", self._on_console), ("pageerror", self._on_pageerror), ("response", self._on_response)):
            try:
                self.page.remove_listener(name, fn)
            except Exception:
                log.debug("ignored error", exc_info=True)

    def mark(self) -> tuple[int, int]:
        return len(self.console_errors), len(self.failed_requests)

    def since(self, mark: tuple[int, int]) -> tuple[list[str], list[dict]]:
        """Console errors and failed requests recorded since ``mark`` (bodies resolved here, outside handlers)."""
        failed = []
        for item in self.failed_requests[mark[1]:]:
            resp = item.pop("_resp", None)
            if item.get("body_excerpt") is None:
                item["body_excerpt"] = _body_excerpt(resp)
            failed.append(dict(item))
        return list(self.console_errors[mark[0]:]), failed


def _body_excerpt(resp, limit: int = 200) -> str:
    if resp is None:
        return ""
    try:
        return " ".join(resp.text()[:limit].split())
    except Exception:
        log.debug("could not read failed response body", exc_info=True)
        return ""


# --------------------------------------------------------------------------- mouse / actions
def _move_to(page, locator, timeout_ms: float) -> None:
    """Smoothly move the pointer to the element's centre (visible cursor in recordings)."""
    locator.wait_for(state="attached", timeout=timeout_ms)
    try:
        locator.scroll_into_view_if_needed(timeout=timeout_ms)
    except Exception:
        log.debug("ignored error", exc_info=True)
    box = locator.bounding_box()
    if box and box["width"] > 0 and box["height"] > 0:
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=MOUSE_STEPS)


def _do_action(page, action, scenario: dict, logs: Path, timeout_ms: float) -> None:
    if not isinstance(action, dict) or len(action) != 1:
        raise ValueError(f"invalid action {action!r}: expected one key")
    (name, arg), = action.items()
    app_url = scenario.get("app_url", "")

    if name == "goto":
        page.goto(_resolve_url(app_url, arg), wait_until="load", timeout=timeout_ms)
    elif name == "click":
        loc = page.locator(_selector(arg)).first
        _move_to(page, loc, timeout_ms)
        loc.click(timeout=timeout_ms)
    elif name == "fill":
        loc = page.locator(_selector(arg)).first
        _move_to(page, loc, timeout_ms)
        loc.click(timeout=timeout_ms)
        loc.fill(str(arg.get("text", "")), timeout=timeout_ms)
    elif name == "type":
        loc = page.locator(_selector(arg)).first
        _move_to(page, loc, timeout_ms)
        loc.click(timeout=timeout_ms)
        loc.press_sequentially(str(arg.get("text", "")), delay=float(arg.get("delay_ms", 30)), timeout=timeout_ms)
    elif name == "press":
        key = arg if isinstance(arg, str) else arg.get("key")
        if not key:
            raise ValueError("press needs a key")
        page.keyboard.press(str(key))
    elif name == "upload":
        loc = page.locator(_selector(arg)).first
        files = arg.get("files") if isinstance(arg, dict) else None
        if isinstance(files, str):
            files = [files]
        if not files:
            raise ValueError("upload needs a non-empty 'files' list")
        loc.set_input_files([_resolve_file(scenario, f) for f in files], timeout=timeout_ms)
    elif name == "hover":
        loc = page.locator(_selector(arg)).first
        _move_to(page, loc, timeout_ms)
        loc.hover(timeout=timeout_ms)
    elif name == "scroll":
        if isinstance(arg, dict) and arg.get("selector"):
            loc = page.locator(str(arg["selector"])).first
            loc.wait_for(state="attached", timeout=timeout_ms)
            loc.scroll_into_view_if_needed(timeout=timeout_ms)
            _move_to(page, loc, timeout_ms)
        else:
            y = arg.get("y", 0) if isinstance(arg, dict) else arg
            page.mouse.wheel(0, float(y or 0))
        page.wait_for_timeout(250)
    elif name == "wait":
        ms = arg.get("ms", 0) if isinstance(arg, dict) else arg
        page.wait_for_timeout(float(ms or 0))
    elif name == "wait_for":
        if not isinstance(arg, dict):
            raise ValueError("wait_for needs {selector|text, timeout_s}")
        ms = float(arg["timeout_s"]) * 1000.0 if arg.get("timeout_s") else timeout_ms
        if arg.get("selector"):
            page.locator(str(arg["selector"])).first.wait_for(state="visible", timeout=ms)
        elif arg.get("text"):
            page.get_by_text(str(arg["text"])).first.wait_for(state="visible", timeout=ms)
        else:
            raise ValueError("wait_for needs a selector or text")
    elif name == "screenshot":
        shot_name = arg if isinstance(arg, str) else (arg or {}).get("name", "screenshot")
        page.screenshot(path=str(logs / f"{_safe_name(shot_name)}.png"))
    else:
        raise ValueError(f"unknown action '{name}'")


# --------------------------------------------------------------------------- expectations
def _short(text: str, n: int = 80) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def expect_summary(exp: dict) -> str:
    parts = []
    if "text" in exp:
        parts.append(f'text contains "{exp["text"]}"')
    if "not_text" in exp:
        parts.append(f'text does not contain "{exp["not_text"]}"')
    if "selector" in exp:
        s = f"selector {exp['selector']}"
        if "count_min" in exp:
            s += f" count>={exp['count_min']}"
        if "contains" in exp:
            s += f' contains "{exp["contains"]}"'
        parts.append(s)
    if "url_contains" in exp:
        parts.append(f'url contains "{exp["url_contains"]}"')
    return "; ".join(parts) or f"(empty expectation {exp!r})"


def check_expectation(page, exp: dict) -> tuple[bool, str]:
    """Evaluate one expectation once. Returns (ok, observed)."""
    if not isinstance(exp, dict):
        return False, f"invalid expectation {exp!r}"
    ok = True
    observed = []
    known = False
    if "text" in exp or "not_text" in exp:
        body = _body_text(page)
        if "text" in exp:
            known = True
            hit = str(exp["text"]) in body
            ok = ok and hit
            observed.append(f'text "{exp["text"]}" {"found" if hit else "not found"} ({len(body)} chars of page text)')
        if "not_text" in exp:
            known = True
            hit = str(exp["not_text"]) in body
            ok = ok and not hit
            observed.append(f'text "{exp["not_text"]}" {"present" if hit else "absent"}')
    if "selector" in exp:
        known = True
        sel = str(exp["selector"])
        loc = page.locator(sel)
        count = loc.count()
        need = int(exp.get("count_min", 1))
        hit = count >= need
        ok = ok and hit
        obs = f"selector {sel}: {count} element(s)"
        if "contains" in exp:
            texts = loc.all_inner_texts() if count else []
            want = str(exp["contains"])
            has = any(want in t for t in texts)
            ok = ok and has
            sample = _short(texts[0]) if texts else ""
            obs += f', "{want}" {"found" if has else "not found"} in text "{sample}"'
        observed.append(obs)
    if "url_contains" in exp:
        known = True
        url = page.url
        hit = str(exp["url_contains"]) in url
        ok = ok and hit
        observed.append(f"url {url}")
    if not known:
        return False, f"unsupported expectation keys {sorted(exp)}"
    return ok, "; ".join(observed)


def _poll_expectations(page, expects: list, deadline_mono: float, clock) -> tuple[bool, str, list | None]:
    """Poll until all expectations hold or the deadline passes. Returns (ok, observed, wait_window)."""
    t0 = clock()
    while True:
        all_ok = True
        observed = []
        for exp in expects:
            try:
                ok, obs = check_expectation(page, exp)
            except Exception as exc:  # navigation in flight, bad selector, ...
                log.debug("expectation check raised; will retry", exc_info=True)
                ok, obs = False, _one_line(exc)
            all_ok = all_ok and ok
            observed.append(obs)
        if all_ok or time.monotonic() >= deadline_mono:
            break
        page.wait_for_timeout(POLL_MS)
    t1 = clock()
    window = [round(t0, 3), round(t1, 3)] if (t1 - t0) > WAIT_WINDOW_MIN_S else None
    return all_ok, "; ".join(observed) if observed else "(no expectations)", window


# --------------------------------------------------------------------------- login
def login(page, scenario: dict) -> str | None:
    """Perform the scenario login. Returns ``None`` on success, else a one-line error."""
    cfg = scenario.get("login") or {"type": "none"}
    kind = str(cfg.get("type", "none")).lower()
    if kind == "none":
        return None
    try:
        user_env = cfg.get("username_env")
        pass_env = cfg.get("password_env")
        username = os.environ.get(user_env, "") if user_env else str(cfg.get("username", ""))
        password = os.environ.get(pass_env, "") if pass_env else str(cfg.get("password", ""))
        if user_env and user_env not in os.environ:
            return f"login: environment variable {user_env} is not set"
        if pass_env and pass_env not in os.environ:
            return f"login: environment variable {pass_env} is not set"
        if kind == "basic":
            token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
            page.set_extra_http_headers({"Authorization": f"Basic {token}"})
            return None
        if kind == "form":
            url = _resolve_url(scenario.get("app_url", ""), cfg.get("url") or "/")
            page.goto(url, wait_until="load", timeout=LOGIN_TIMEOUT_MS)
            for sel, value in ((cfg.get("username_selector"), username), (cfg.get("password_selector"), password)):
                if not sel:
                    return "login: form login needs username_selector and password_selector"
                loc = page.locator(str(sel)).first
                _move_to(page, loc, LOGIN_TIMEOUT_MS)
                loc.click(timeout=LOGIN_TIMEOUT_MS)
                loc.fill(value, timeout=LOGIN_TIMEOUT_MS)
            submit = cfg.get("submit_selector")
            if submit:
                loc = page.locator(str(submit)).first
                _move_to(page, loc, LOGIN_TIMEOUT_MS)
                loc.click(timeout=LOGIN_TIMEOUT_MS)
            else:
                page.keyboard.press("Enter")
            success = cfg.get("success_selector")
            if success:
                page.locator(str(success)).first.wait_for(state="visible", timeout=LOGIN_TIMEOUT_MS)
            else:
                page.wait_for_load_state("load", timeout=LOGIN_TIMEOUT_MS)
            return None
        return f"login: unsupported type '{kind}' (expected none, form or basic)"
    except Exception as exc:
        log.debug("handled error", exc_info=True)
        return f"login failed: {_one_line(exc)}"


# --------------------------------------------------------------------------- step execution
def _new_result(step: dict, index: int) -> dict:
    sid = str(step.get("id", f"step{index + 1}"))
    expects = step.get("expect") or []
    return {
        "id": sid,
        "title": str(step.get("title", sid)),
        "status": "SKIPPED",
        "expected": "; ".join(expect_summary(e) for e in expects) if expects else "(no expectations)",
        "observed": "",
        "screenshot": None,
        "seconds": 0.0,
        "error": None,
        "t_start": None,
        "t_end": None,
        "wait_windows": [],
        "console_errors": [],
        "failed_requests": [],
    }


def skipped_results(scenario: dict, reason: str, clock=None) -> list[dict]:
    """Result list with every step SKIPPED (used when login fails before any step)."""
    t = clock() if clock else 0.0
    results = []
    for i, step in enumerate(scenario.get("steps") or []):
        r = _new_result(step, i)
        r["error"] = reason
        r["t_start"] = r["t_end"] = t
        results.append(r)
    return results


def run_steps(page, scenario: dict, out: Path, clock=None, pacer=None, screenshot_prefix: str = "step",
              do_login: bool = True) -> list[dict]:
    """Execute every step: actions, then expectations, then a screenshot.

    ``clock()`` returns seconds since capture start (a local clock is used when
    ``None``); ``pacer(step_index, step_id)`` blocks until a step may start.
    After a FAIL the remaining steps are SKIPPED.
    """
    paths = _paths(out)
    logs = paths.logs
    clock = clock or _local_clock()
    steps = scenario.get("steps") or []

    if do_login:
        err = login(page, scenario)
        if err:
            return skipped_results(scenario, err, clock)

    collector = _Collector(page)
    collector.attach()
    results: list[dict] = []
    failed_reason: str | None = None
    try:
        for index, step in enumerate(steps):
            res = _new_result(step, index)
            if failed_reason:
                res["error"] = f"skipped: {failed_reason}"
                res["t_start"] = res["t_end"] = clock()
                results.append(res)
                continue
            if pacer is not None:
                pacer(index, res["id"])
            mark = collector.mark()
            wall0 = time.monotonic()
            res["t_start"] = clock()
            timeout_s = float(step.get("timeout_s") or DEFAULT_STEP_TIMEOUT_S)
            deadline = wall0 + timeout_s
            err = None
            try:
                for action in step.get("actions") or []:
                    remaining_ms = max(1000.0, (deadline - time.monotonic()) * 1000.0)
                    _do_action(page, action, scenario, logs, remaining_ms)
                ok, observed, window = _poll_expectations(page, step.get("expect") or [], deadline, clock)
                res["observed"] = observed
                if window:
                    res["wait_windows"].append(window)
                if not ok:
                    err = f"expectation not met within {timeout_s:g} s: {observed}"
            except Exception as exc:
                log.debug("handled error", exc_info=True)
                err = _one_line(exc)
            shot = logs / f"{screenshot_prefix}-{index + 1:02d}-{_safe_name(res['id'])}.png"
            try:
                page.screenshot(path=str(shot))
                res["screenshot"] = str(shot)
            except Exception:
                log.debug("handled error", exc_info=True)
                res["screenshot"] = None
            res["t_end"] = clock()
            res["seconds"] = round(time.monotonic() - wall0, 3)
            res["console_errors"], res["failed_requests"] = collector.since(mark)
            if err:
                res["status"] = "FAIL"
                res["error"] = err
                failed_reason = f"step '{res['id']}' failed"
                try:
                    (logs / f"failure-{_safe_name(res['id'])}.html").write_text(page.content(), encoding="utf-8")
                except Exception:
                    log.debug("ignored error", exc_info=True)
            else:
                res["status"] = "PASS"
            results.append(res)
    finally:
        collector.detach()
    return results


# --------------------------------------------------------------------------- dryrun
def _write_results_md(path: Path, scenario: dict, result: dict) -> None:
    def cell(text) -> str:
        return " ".join(str("" if text is None else text).split()).replace("|", "\\|")

    lines = [
        f"# Smoke results: {scenario.get('name', scenario.get('slug', 'scenario'))}",
        "",
        f"- Verdict: **{result['verdict']}**",
        f"- Attempts: {result['attempts']}",
        f"- App: {scenario.get('app_url', '')}",
        f"- Generated: {_dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec='seconds')}",
        "",
        "| # | Step | Title | Status | Expected | Observed | Seconds | Screenshot | Error |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for i, s in enumerate(result["steps"], start=1):
        shot = Path(s["screenshot"]).name if s.get("screenshot") else ""
        lines.append(
            f"| {i} | {cell(s['id'])} | {cell(s['title'])} | {s['status']} | {cell(s['expected'])} | "
            f"{cell(_short(s['observed'], 160))} | {s['seconds']:.1f} | {cell(shot)} | {cell(s.get('error') or '')} |"
        )
    if result.get("notes"):
        lines += ["", "## Notes", ""] + [f"- {cell(n)}" for n in result["notes"]]
    lines += ["", "## Console errors", ""]
    lines += [f"- {cell(_short(e, 300))}" for e in result["console_errors"]] or ["- none"]
    lines += ["", "## Failed requests", ""]
    lines += [f"- {r['status']} {cell(r['url'])} {cell(_short(r.get('body_excerpt', ''), 120))}" for r in result["failed_requests"]] or ["- none"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _launch(scenario: dict, out: Path, headless: bool):
    from demo_smoke import chrome as chrome_mod  # lazy: keeps import cycles away

    try:
        return chrome_mod.launch(Path(out), scenario.get("viewport") or {}, headless=headless)
    except Exception as exc:
        raise DriveError(f"could not launch Chrome: {_one_line(exc)}") from exc


def _install_cursor(session) -> None:
    try:
        from demo_smoke import cursor as cursor_mod

        cursor_mod.install(session.cdp)
    except Exception:
        log.debug("ignored error", exc_info=True)


def dryrun(scenario: dict, out: Path, headless: bool = False) -> dict:
    """Drive the scenario (retrying the whole thing once on FAIL) and write the smoke report."""
    out = Path(out)
    paths = _paths(out)
    notes: list[str] = []
    result: dict = {}
    attempts = 0
    for attempt in (1, 2):
        attempts = attempt
        session = _launch(scenario, out, headless)
        try:
            _install_cursor(session)
            steps = run_steps(session.page, scenario, out, screenshot_prefix="step")
        finally:
            session.close()
        verdict = "PASS" if steps and all(s["status"] == "PASS" for s in steps) else "FAIL"
        result = {
            "verdict": verdict,
            "steps": steps,
            "console_errors": [e for s in steps for e in s.get("console_errors", [])],
            "failed_requests": [r for s in steps for r in s.get("failed_requests", [])],
            "attempts": attempts,
        }
        if verdict == "PASS":
            break
        bad = next((s for s in steps if s["status"] == "FAIL"), None)
        notes.append(
            f"attempt {attempt} failed at step '{bad['id']}': {bad['error']}" if bad
            else f"attempt {attempt} failed: {(steps[0]['error'] if steps else 'no steps')}"
        )
    result["exit_code"] = 0 if result["verdict"] == "PASS" else 2
    result["notes"] = notes
    result["scenario"] = scenario.get("slug") or scenario.get("name")
    result["headless"] = bool(headless)
    _write_results_md(paths.logs / "smoke-results.md", scenario, result)
    (paths.logs / "dryrun.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return result


# --------------------------------------------------------------------------- record
def _fallback_next_start(prev_t_start: float, prev_t_end: float, prev_duration: float, gap: float = 0.3) -> float:
    return max(prev_t_end + gap, prev_t_start + prev_duration)


def _duration_of(durations: dict, key: str) -> float:
    for k in (key, f"seg-{key}"):
        if k in durations:
            try:
                return max(0.0, float(durations[k] or 0.0))
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def record(scenario: dict, out: Path, capture: str, headless: bool, durations: dict) -> dict:
    """Paced run with video capture; writes raw/capture.mp4 and logs/markers.json, returns the markers dict."""
    from demo_smoke import capture as capture_mod

    try:
        from demo_smoke.pacing import next_start
    except ImportError:
        next_start = _fallback_next_start

    out = Path(out)
    paths = _paths(out)
    durations = durations or {}
    steps_cfg = scenario.get("steps") or []
    session = _launch(scenario, out, headless)
    try:
        _install_cursor(session)
        page = session.page
        login_error = login(page, scenario)
        if not login_error:
            try:  # hold the first screen while the intro is spoken
                page.goto(_resolve_url(scenario.get("app_url", ""), "/"), wait_until="load", timeout=LOGIN_TIMEOUT_MS)
            except Exception:
                log.debug("ignored error", exc_info=True)
        try:
            cap = capture_mod.make(capture, session, out)
            cap.start()
        except Exception as exc:
            raise DriveError(f"could not start {capture} capture: {_one_line(exc)}") from exc

        state: dict = {"t_start": None, "id": None}

        def pacer(index: int, step_id: str) -> None:
            if index == 0 or state["t_start"] is None:
                target = _duration_of(durations, "intro")
            else:
                target = next_start(state["t_start"], cap.now(), _duration_of(durations, state["id"]))
            _wait_until(page, cap.now, target)
            state["t_start"] = cap.now()
            state["id"] = step_id

        if login_error:
            steps = skipped_results(scenario, login_error, cap.now)
        else:
            steps = run_steps(page, scenario, out, clock=cap.now, pacer=pacer, screenshot_prefix="record",
                              do_login=False)

        executed = [s for s in steps if s["status"] != "SKIPPED" and s["t_start"] is not None]
        if executed:
            last = executed[-1]
            outro_t = max(float(last["t_end"]), float(last["t_start"]) + _duration_of(durations, last["id"]))
        else:
            outro_t = max(cap.now(), _duration_of(durations, "intro"))
        end_t = outro_t + _duration_of(durations, "outro")
        _wait_until(page, cap.now, end_t + TAIL_S)
        try:
            video = cap.stop()
        except Exception as exc:
            raise DriveError(f"capture failed: {_one_line(exc)}") from exc
    finally:
        session.close()

    markers = _build_markers(cap.capture_start_epoch or time.time(), steps, outro_t, end_t)
    markers["capture"] = str(video)
    markers["capture_backend"] = capture
    markers["capture_seconds"] = round(cap.now(), 3)
    markers["viewport"] = dict(session.viewport)
    markers["note"] = getattr(cap, "note", "") or ""
    markers["verdict"] = "PASS" if steps and all(s["status"] == "PASS" for s in steps) else "FAIL"
    if login_error:
        markers["error"] = login_error
    _save_markers(markers, out, paths)
    if not steps_cfg:
        markers["note"] = (markers["note"] + "; " if markers["note"] else "") + "scenario has no steps"
    return markers


def _build_markers(capture_start_epoch: float, steps: list[dict], outro_t: float, end_t: float) -> dict:
    try:
        from demo_smoke import markers as mk

        m = mk.new(capture_start_epoch)
        for s in steps:
            mk.add_step(m, s["id"], _num(s["t_start"]), _num(s["t_end"]), s["status"], list(s.get("wait_windows") or []))
        m["intro_t"] = 0.0
        m["outro_t"] = round(outro_t, 3)
        m["end_t"] = round(end_t, 3)
        return m
    except Exception:
        log.debug("handled error", exc_info=True)
        return {
            "capture_start_epoch": capture_start_epoch,
            "intro_t": 0.0,
            "outro_t": round(outro_t, 3),
            "end_t": round(end_t, 3),
            "steps": [
                {"id": s["id"], "t_start": _num(s["t_start"]), "t_end": _num(s["t_end"]), "status": s["status"],
                 "wait_windows": list(s.get("wait_windows") or [])}
                for s in steps
            ],
        }


def _num(v) -> float:
    try:
        return round(float(v), 3)
    except (TypeError, ValueError):
        return 0.0


def _save_markers(markers: dict, out: Path, paths) -> Path:
    try:
        from demo_smoke import markers as mk

        return Path(mk.save(markers, Path(out)))
    except Exception:
        log.debug("handled error", exc_info=True)
        path = paths.logs / "markers.json"
        path.write_text(json.dumps(markers, indent=2, default=str), encoding="utf-8")
        return path
