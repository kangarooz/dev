"""Onboarding subcommands: ``creds set|list|check``, ``init-scenario``, ``validate``, ``inspect``.

Wired into the CLI through ``register(subparsers, run_map)``; ``main(argv)``
runs them standalone (``python -m demo_smoke.onboard_scenario ...``) with the
same exit codes as ``cli.py``: 0 ok, 2 feature failed (login), 3 tooling
error, 4 bad input.

Scaffold note: ``init-scenario`` writes a ``todo`` field on every step that
still needs selectors.  ``scenario.validate`` rejects unknown step keys, so the
``validate`` command here strips ``todo`` (reporting it as a warning) before
delegating; ``dryrun``/``record`` accept the file once the todos are resolved
and removed, which is the intended hand-off.
"""

from __future__ import annotations

import argparse
import builtins
import getpass
import json
import re
import sys
import time
from pathlib import Path

from . import dotenv
from . import scenario as scenario_mod

EXIT_OK = 0
EXIT_FAIL = 2
EXIT_ERROR = 3
EXIT_BAD_INPUT = 4
DEFAULT_OUT = "demo-output"
INSPECT_MAX_ROWS = 60
INSPECT_SETTLE_MS = 500
INSPECT_TIMEOUT_MS = 30_000
LOGIN_TYPES = ("none", "form", "basic")
STEP_KEYS = ("id", "title", "narration", "actions", "expect", "timeout_s")
TODO_KEY = "todo"
URL_RE = re.compile(r"^https?://\S+$")
_IDENT_RE = re.compile(r"^-?[A-Za-z_][A-Za-z0-9_-]*$")
_PLAIN_ATTR_RE = re.compile(r"^[A-Za-z0-9_./:-]+$")

# Elements ``inspect`` reports, in this priority when the page has more than the row cap.
_INSPECT_JS = r"""
() => {
  const query = 'input, textarea, select, button, a[href], [role=button], [role=link], [role=textbox], [contenteditable]';
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const out = [];
  const seen = new Set();
  Array.from(document.querySelectorAll(query)).forEach((el, index) => {
    if (seen.has(el)) return;
    seen.add(el);
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (tag === 'input' && type === 'hidden') return;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    const visible = rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
    let text = clean(el.innerText !== undefined ? el.innerText : el.textContent);
    if (tag === 'input' && ['submit', 'button', 'reset'].includes(type)) text = clean(el.value) || text;
    if (tag === 'select') text = clean(Array.from(el.options).slice(0, 4).map((o) => o.text).join(' | '));
    let label = '';
    if (el.labels && el.labels.length) label = clean(el.labels[0].innerText);
    const sameTag = Array.from(document.querySelectorAll(tag));
    out.push({
      index, tag, type,
      id: el.id || '',
      name: el.getAttribute('name') || '',
      placeholder: el.getAttribute('placeholder') || '',
      text: text.slice(0, 120),
      aria_label: clean(el.getAttribute('aria-label')),
      label: label.slice(0, 120),
      role: (el.getAttribute('role') || '').toLowerCase(),
      href: tag === 'a' ? (el.getAttribute('href') || '') : '',
      visible, disabled: !!el.disabled,
      tag_index: sameTag.indexOf(el),
    });
  });
  return out;
}
"""


class OnboardError(RuntimeError):
    """Bad input for one of the onboarding commands (exit 4)."""


# --------------------------------------------------------------------------- output helpers


def _say(msg: str) -> None:
    print(msg, flush=True)


def _err(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr, flush=True)


def _warn(msg: str) -> None:
    print(f"warning: {msg}", flush=True)


def _write_log(out: str | None, cmd: str, data: dict) -> Path | None:
    if not out:
        return None
    try:
        from .env import Paths

        p = Paths(out).logs / f"{cmd}.json"
        p.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        return p
    except OSError:
        return None


# --------------------------------------------------------------------------- creds


def check_name(name: str) -> str:
    if not dotenv.NAME_RE.match(name or ""):
        raise OnboardError(f"invalid credential name {name!r}: use uppercase letters, digits and "
                           "underscores, e.g. DEMO_PASS")
    return name


def _read_secret(name: str, from_stdin: bool) -> str:
    if from_stdin:
        value = sys.stdin.read()
        if value.endswith("\r\n"):
            value = value[:-2]
        elif value.endswith("\n"):
            value = value[:-1]
        return value
    return getpass.getpass(f"{name}: ")


def creds_set(name: str, env_file: str | None, value_from_stdin: bool) -> int:
    check_name(name)
    value = _read_secret(name, value_from_stdin)
    if value == "":
        raise OnboardError(f"{name}: empty value (nothing written)")
    p = dotenv.write_value(env_file, name, value)
    kind = "op:// reference, resolved at run time" if dotenv.is_op_ref(value) else "value stored"
    _say(f"creds: set {name} in {p} ({kind})")
    return EXIT_OK


def creds_list(env_file: str | None) -> int:
    p = dotenv.env_path(env_file)
    found = dotenv.names(env_file)
    if not found:
        _say(f"creds: no names in {p}")
        return EXIT_OK
    for n in found:
        _say(n)
    return EXIT_OK


def creds_check(names: list[str], env_file: str | None) -> int:
    missing: list[str] = []
    for name in names:
        check_name(name)
        value, source = dotenv.resolve(name, env_file)
        if value is None:
            missing.append(name)
            why = "not in the environment or .env" if source == "missing" else source
            _say(f"{name}: MISSING ({why})")
        else:
            _say(f"{name}: ok ({source})")
    if missing:
        _err("creds: MISSING " + ", ".join(missing)
             + " (run `python -m demo_smoke creds set NAME` in your own shell)")
        return EXIT_BAD_INPUT
    _say(f"creds: ok {len(names)}/{len(names)} resolvable")
    return EXIT_OK


def cmd_creds(args) -> int:
    try:
        sub = getattr(args, "creds_cmd", None)
        if sub == "set":
            return creds_set(args.name, args.env_file, args.value_from_stdin)
        if sub == "list":
            return creds_list(args.env_file)
        if sub == "check":
            return creds_check(list(args.names), args.env_file)
        raise OnboardError("creds needs a subcommand: set, list or check")
    except OnboardError as e:
        _err(str(e))
        return EXIT_BAD_INPUT
    except OSError as e:
        _err(f"creds: {e}")
        return EXIT_ERROR


# --------------------------------------------------------------------------- init-scenario


def slugify(name: str) -> str:
    return scenario_mod.slugify(name)


def step_id(title: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "step"
    if not re.match(r"^[a-z0-9]", base):
        base = "s-" + base
    sid, n = base, 2
    while sid in taken:
        sid = f"{base}-{n}"
        n += 1
    taken.add(sid)
    return sid


def parse_step_arg(text: str) -> tuple[str, str]:
    """``"Title :: plain English"`` -> (title, description); no ``::`` -> both the text."""
    if "::" in text:
        title, desc = text.split("::", 1)
        title, desc = title.strip(), desc.strip()
        return title or desc, desc or title
    text = text.strip()
    return text, text


def draft_narration(title: str, desc: str) -> str:
    sentence = desc if desc else title
    sentence = sentence[0].upper() + sentence[1:] if sentence else title
    if not sentence.endswith((".", "!", "?")):
        sentence += "."
    return sentence


def make_step(title: str, desc: str, taken: set[str]) -> dict:
    return {
        "id": step_id(title, taken),
        "title": title,
        "narration": draft_narration(title, desc),
        "actions": [],
        "expect": [],
        TODO_KEY: f"{desc} -- fill actions/expect with selectors from `inspect`, then remove this key",
    }


def scaffold(name: str, url: str, steps: list[tuple[str, str]], login: str = "none",
             username_env: str | None = None, password_env: str | None = None,
             login_url: str | None = None, username_selector: str | None = None,
             password_selector: str | None = None, submit_selector: str | None = None,
             success_selector: str | None = None) -> dict:
    """The scenario dict ``init-scenario`` writes (pure; raises OnboardError on bad input)."""
    name = (name or "").strip()
    if not name:
        raise OnboardError("--name must not be empty")
    url = (url or "").strip().rstrip("/") or url
    if not URL_RE.match(url or ""):
        raise OnboardError(f"--url must be an http(s) URL, got {url!r}")
    if login not in LOGIN_TYPES:
        raise OnboardError(f"--login must be one of: {', '.join(LOGIN_TYPES)}")
    slug = slugify(name)
    login_cfg: dict = {"type": login}
    if login != "none":
        u_env = check_name(username_env or "DEMO_USER")
        p_env = check_name(password_env or "DEMO_PASS")
        if login == "form":
            login_cfg.update({
                "url": login_url or "/login",
                "username_selector": username_selector or "input[name=username]",
                "password_selector": password_selector or "input[name=password]",
                "submit_selector": submit_selector or "button[type=submit]",
            })
            if success_selector:
                login_cfg["success_selector"] = success_selector
        login_cfg.update({"username_env": u_env, "password_env": p_env})
    taken: set[str] = set()
    if not steps:
        steps = [("Open the app", "open the app and show its main screen")]
    data = {
        "$schema": "./schema.json",
        "name": name,
        "slug": slug,
        "app_url": url,
        "viewport": {"width": 1920, "height": 1080},
        "login": login_cfg,
        "max_length_seconds": 90,
        "intro": f"This is a short walkthrough of {name}, running on a local build.",
        "outro": f"That was {name}: every step ran against the local build, end to end.",
        "steps": [make_step(t, d, taken) for t, d in steps],
    }
    return data


def strip_todos(data: dict) -> list[str]:
    """Remove ``todo`` from every step in place; return the removed texts by step id."""
    todos: list[str] = []
    for step in data.get("steps", []) if isinstance(data.get("steps"), list) else []:
        if isinstance(step, dict) and TODO_KEY in step:
            todos.append(f"{step.get('id', '?')}: {step.pop(TODO_KEY)}")
    return todos


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    answer = builtins.input(f"{prompt}{suffix}: ").strip()
    return answer or default


def interactive_answers(args) -> dict:
    """Fill the missing init-scenario answers from the terminal (``input()``)."""
    name = args.name or _ask("Feature name (e.g. Chat with Manuals)")
    url = args.url or _ask("App URL", "http://localhost:3000")
    login = args.login or _ask("Login type (none, form, basic)", "none")
    username_env = args.username_env
    password_env = args.password_env
    if login != "none":
        username_env = username_env or _ask("Env var NAME for the username", "DEMO_USER")
        password_env = password_env or _ask("Env var NAME for the password", "DEMO_PASS")
    steps = [parse_step_arg(s) for s in (args.step or [])]
    if not steps:
        _say("Describe the happy path, one step at a time (blank title to finish).")
        n = 1
        while True:
            title = _ask(f"Step {n} title")
            if not title:
                break
            desc = _ask("  What happens / what should be visible", title)
            steps.append((title, desc))
            n += 1
    return {"name": name, "url": url, "login": login, "username_env": username_env,
            "password_env": password_env, "steps": steps}


def cmd_init_scenario(args) -> int:
    try:
        if args.interactive:
            ans = interactive_answers(args)
        else:
            if not args.name or not args.url:
                raise OnboardError("--name and --url are required (or use --interactive)")
            ans = {"name": args.name, "url": args.url, "login": args.login or "none",
                   "username_env": args.username_env, "password_env": args.password_env,
                   "steps": [parse_step_arg(s) for s in (args.step or [])]}
        data = scaffold(ans["name"], ans["url"], ans["steps"], login=ans["login"],
                        username_env=ans["username_env"], password_env=ans["password_env"],
                        login_url=args.login_url, username_selector=args.username_selector,
                        password_selector=args.password_selector,
                        submit_selector=args.submit_selector,
                        success_selector=args.success_selector)
        # the scaffold must be valid apart from the todo markers
        probe = json.loads(json.dumps(data))
        strip_todos(probe)
        errors = scenario_mod.validate(probe)
        if errors:
            raise OnboardError("scaffold would be invalid: " + "; ".join(errors))
        out = Path(args.scenario_out) if args.scenario_out else Path("scenarios") / f"{data['slug']}.json"
        if out.exists() and not args.force:
            raise OnboardError(f"{out} exists (pass --force to overwrite)")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OnboardError as e:
        _err(str(e))
        return EXIT_BAD_INPUT
    except (EOFError, KeyboardInterrupt):
        _err("init-scenario: cancelled")
        return 130
    except OSError as e:
        _err(f"init-scenario: {e}")
        return EXIT_ERROR
    n = len(data["steps"])
    _say(f"init-scenario: ok -> {out} ({n} step{'s' if n != 1 else ''}, {n} todo, login={data['login']['type']})")
    if data["login"]["type"] != "none":
        _say(f"  next: python -m demo_smoke creds set {data['login']['username_env']} && "
             f"python -m demo_smoke creds set {data['login']['password_env']}")
        if data["login"]["type"] == "form":
            _say("  note: login selectors are placeholders; check them with "
                 f"`python -m demo_smoke inspect {data['app_url']}{data['login']['url']}`")
    _say(f"  next: python -m demo_smoke inspect {data['app_url']} ; fill actions/expect ; "
         f"python -m demo_smoke validate {out}")
    return EXIT_OK


# --------------------------------------------------------------------------- validate


def _action_summary(actions: list) -> str:
    parts = []
    for a in actions:
        if isinstance(a, dict) and len(a) == 1:
            parts.append(next(iter(a)))
        else:
            parts.append("?")
    return ",".join(parts) if parts else "-"


def validate_file(path: str | Path, env_file: str | None = None) -> dict:
    """Validate a scenario file: ``{"errors": [...], "warnings": [...], "steps": [...], "data": dict|None}``."""
    p = Path(path)
    res: dict = {"path": str(p), "errors": [], "warnings": [], "steps": [], "data": None}
    if not p.is_file():
        res["errors"].append(f"scenario file not found: {p}")
        return res
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        res["errors"].append(f"invalid JSON at line {e.lineno} column {e.colno}: {e.msg}")
        return res
    except OSError as e:
        res["errors"].append(f"cannot read: {e}")
        return res
    if not isinstance(data, dict):
        res["errors"].append("scenario must be a JSON object")
        return res
    todos = strip_todos(data)
    res["errors"] = scenario_mod.validate(data)
    res["data"] = data
    for t in todos:
        res["warnings"].append(f"step {t}")
    if res["errors"]:
        return res
    login = data.get("login") or {"type": "none"}
    for key in ("username_env", "password_env"):
        var = login.get(key)
        if var:
            value, source = dotenv.lookup(var, env_file)
            if value is None:
                res["warnings"].append(f"login.{key} {var} is not set (environment or .env): "
                                       f"run `python -m demo_smoke creds set {var}`")
            elif dotenv.is_op_ref(value) and not dotenv.op_path():
                res["warnings"].append(f"login.{key} {var} is an op:// reference but `op` is not on PATH")
    base = p.resolve().parent
    for i, step in enumerate(data["steps"], 1):
        actions = step.get("actions") or []
        expect = step.get("expect") or []
        res["steps"].append({"n": i, "id": step["id"], "title": step["title"],
                             "actions": len(actions), "expect": len(expect),
                             "summary": _action_summary(actions)})
        if not actions:
            res["warnings"].append(f"step {step['id']}: no actions")
        if not expect:
            res["warnings"].append(f"step {step['id']}: no expectations (nothing is checked)")
        for a in actions:
            if isinstance(a, dict) and "upload" in a:
                for f in a["upload"].get("files", []):
                    fp = Path(f)
                    if not fp.is_absolute():
                        fp = base / fp
                    if not fp.is_file():
                        res["warnings"].append(f"step {step['id']}: upload file not found: {fp}")
    return res


def cmd_validate(args) -> int:
    res = validate_file(args.scenario, getattr(args, "env_file", None))
    log = {k: v for k, v in res.items() if k != "data"}
    if res["errors"]:
        log.update({"error": "validate: INVALID " + "; ".join(res["errors"]), "exit_code": EXIT_BAD_INPUT})
        _write_log(getattr(args, "out", None), "validate", log)
        _say(f"validate: INVALID {res['path']} ({len(res['errors'])} error"
             f"{'s' if len(res['errors']) != 1 else ''})")
        for e in res["errors"]:
            _err(e)
        return EXIT_BAD_INPUT
    _write_log(getattr(args, "out", None), "validate", log)
    data = res["data"]
    n_w = len(res["warnings"])
    _say(f"validate: ok {res['path']} {data['name']!r} {len(res['steps'])} steps, "
         f"login={(data.get('login') or {}).get('type', 'none')}, {n_w} warning{'s' if n_w != 1 else ''}")
    for s in res["steps"]:
        _say(f"  {s['n']:2d}. {s['id']:<20} {s['title']}  (actions={s['actions']} [{s['summary']}] "
             f"expect={s['expect']})")
    for w in res["warnings"]:
        _warn(w)
    return EXIT_OK


# --------------------------------------------------------------------------- inspect


def _css_attr(value: str) -> str:
    if _PLAIN_ATTR_RE.match(value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _has_text(text: str, limit: int = 40) -> str:
    t = text.strip()
    if len(t) > limit:
        cut = t[:limit]
        t = cut.rsplit(" ", 1)[0] if " " in cut else cut
    return t.replace("\\", "\\\\").replace('"', '\\"')


def classify(el: dict) -> str:
    tag, typ, role = el.get("tag", ""), el.get("type", ""), el.get("role", "")
    if tag == "input" and typ == "file":
        return "file"
    if tag == "button" or (tag == "input" and typ in ("submit", "button", "reset", "image")) or role == "button":
        return "button"
    if tag == "a" or role == "link":
        return "link"
    if tag == "select":
        return "select"
    if tag == "textarea" or role == "textbox" or (tag not in ("input",) and el.get("contenteditable")):
        return "textarea"
    if tag == "input":
        return f"input:{typ or 'text'}"
    return "textarea" if tag not in ("input", "select") else tag


def selector_candidates(el: dict) -> list[str]:
    """Stable selector guesses, best first: #id, [name], [placeholder], type, text, aria, position."""
    tag = el.get("tag") or "*"
    kind = classify(el)
    cands: list[str] = []
    if el.get("id"):
        i = el["id"]
        cands.append(f"#{i}" if _IDENT_RE.match(i) and not i[0].isdigit() else f'[id="{i}"]')
    if el.get("name"):
        cands.append(f"{tag}[name={_css_attr(el['name'])}]")
    if el.get("placeholder"):
        cands.append(f"{tag}[placeholder={_css_attr(el['placeholder'])}]")
    if kind == "file":
        cands.append("input[type=file]")
    text = el.get("text") or ""
    if kind in ("button", "link") and text:
        base = tag if tag in ("button", "a") else f"[role={el.get('role') or 'button'}]"
        cands.append(f'{base}:has-text("{_has_text(text)}")')
    if kind == "button" and tag == "input" and el.get("type"):
        cands.append(f"input[type={el['type']}]")
    if kind == "link" and el.get("href"):
        cands.append(f"a[href={_css_attr(el['href'])}]")
    if el.get("aria_label"):
        cands.append(f"{tag}[aria-label={_css_attr(el['aria_label'])}]")
    if kind.startswith("input:") and el.get("type") and el["type"] != "text":
        cands.append(f"input[type={el['type']}]")
    if el.get("tag_index", -1) >= 0:
        cands.append(f"{tag} >> nth={el['tag_index']}")
    return cands


def choose_selector(el: dict, count_fn) -> tuple[str, int]:
    """First candidate that matches exactly one element; else the best one with its count."""
    best: tuple[str, int] | None = None
    for cand in selector_candidates(el):
        try:
            n = int(count_fn(cand))
        except Exception:  # noqa: BLE001 - an unsupported selector just is not a candidate
            continue
        if n == 1:
            return cand, 1
        if best is None or (n > 0 and best[1] == 0):
            best = (cand, n)
    return best or (f"{el.get('tag', '*')} >> nth={max(el.get('tag_index', 0), 0)}", 0)


def collect_elements(page, max_rows: int = INSPECT_MAX_ROWS, include_hidden: bool = False) -> tuple[list[dict], int]:
    """Interactive elements with a selector each, capped at ``max_rows`` (links are dropped first)."""
    raw = page.evaluate(_INSPECT_JS) or []
    rows = [el for el in raw if include_hidden or el.get("visible") or classify(el) == "file"]
    total = len(rows)
    if len(rows) > max_rows:
        keep = sorted(rows, key=lambda e: (classify(e) == "link", e["index"]))[:max_rows]
        rows = sorted(keep, key=lambda e: e["index"])

    def count(sel: str) -> int:
        return page.locator(sel).count()

    out = []
    for el in rows:
        sel, n = choose_selector(el, count)
        kind = classify(el)
        hint = el.get("placeholder") or el.get("label") or el.get("aria_label") if kind not in ("button", "link") \
            else el.get("text") or el.get("aria_label")
        out.append({
            "kind": kind, "tag": el["tag"], "type": el.get("type", ""), "selector": sel, "unique": n == 1,
            "matches": n, "id": el.get("id", ""), "name": el.get("name", ""),
            "placeholder": el.get("placeholder", ""), "text": el.get("text", ""), "label": el.get("label", ""),
            "href": el.get("href", ""), "visible": bool(el.get("visible")), "disabled": bool(el.get("disabled")),
            "hint": hint or "",
        })
    return out, total


def format_table(elements: list[dict]) -> list[str]:
    lines = [f"  {'#':>2}  {'kind':<12} {'selector':<44} text / placeholder"]
    for i, e in enumerate(elements, 1):
        sel = e["selector"] + ("" if e["unique"] else f"  (x{e['matches']})")
        extra = e["hint"]
        if e["kind"] == "link" and e.get("href"):
            extra = f"{extra} -> {e['href']}" if extra else e["href"]
        if e.get("disabled"):
            extra += " [disabled]"
        if not e.get("visible"):
            extra += " [hidden]"
        lines.append(f"  {i:>2}  {e['kind']:<12} {sel:<44} {extra[:60]}")
    return lines


def inspect_url(url: str, out: Path, headless: bool = False, login_from: str | None = None,
                max_rows: int = INSPECT_MAX_ROWS, settle_ms: int = INSPECT_SETTLE_MS,
                include_hidden: bool = False, env_file: str | None = None) -> dict:
    """Open ``url`` in Chrome (optionally after the scenario's login) and list its interactive elements.

    Raises ``OnboardError`` (bad input) or ``RuntimeError`` (Chrome/login problem);
    a failed login returns ``{"error": ..., "login_failed": True}``.
    """
    from . import chrome as chrome_mod
    from . import drive as drive_mod

    scen = None
    viewport = dict(chrome_mod.DEFAULT_VIEWPORT)
    if login_from:
        scen = scenario_mod.load(login_from)
        viewport = scen.get("viewport") or viewport
        dotenv.load_env(env_file)
        if not re.match(r"^[a-z][a-z0-9+.-]*:", url, re.IGNORECASE):
            url = drive_mod._resolve_url(scen.get("app_url", ""), url)
    if not URL_RE.match(url):
        raise OnboardError(f"URL must be http(s), got {url!r}")
    t0 = time.time()
    session = chrome_mod.launch(Path(out), viewport, headless=headless)
    try:
        page = session.page
        if scen is not None:
            problem = drive_mod.login(page, scen)
            if problem:
                return {"url": url, "error": problem, "login_failed": True, "elements": [], "total": 0}
        page.goto(url, wait_until="load", timeout=INSPECT_TIMEOUT_MS)
        page.wait_for_timeout(settle_ms)
        elements, total = collect_elements(page, max_rows=max_rows, include_hidden=include_hidden)
        return {"url": url, "final_url": page.url, "title": page.title(), "elements": elements,
                "total": total, "shown": len(elements), "headless": headless,
                "login_from": login_from, "seconds": round(time.time() - t0, 1)}
    finally:
        session.close()


def cmd_inspect(args) -> int:
    out = args.out or DEFAULT_OUT
    try:
        res = inspect_url(args.url, Path(out), headless=args.headless, login_from=args.login_from,
                          max_rows=args.max, settle_ms=args.settle_ms, include_hidden=args.all,
                          env_file=getattr(args, "env_file", None))
    except (OnboardError, scenario_mod.ScenarioError) as e:
        _write_log(out, "inspect", {"error": f"inspect: {e}", "exit_code": EXIT_BAD_INPUT})
        _err(str(e))
        return EXIT_BAD_INPUT
    except Exception as e:  # noqa: BLE001 - Chrome/Playwright failures become one line, exit 3
        _write_log(out, "inspect", {"error": f"inspect: {e}", "exit_code": EXIT_ERROR})
        _err(f"inspect: {e}")
        return EXIT_ERROR
    if res.get("login_failed"):
        res.update({"exit_code": EXIT_FAIL})
        _write_log(out, "inspect", res)
        _say(f"inspect: FAIL {res['error']}")
        return EXIT_FAIL
    _write_log(out, "inspect", res)
    if args.json:
        _say(json.dumps(res, indent=2, ensure_ascii=False))
        return EXIT_OK
    _say(f"inspect: ok {res['total']} interactive elements at {res['url']} "
         f"({res['shown']} shown, title={res.get('title', '')!r})")
    for line in format_table(res["elements"]):
        _say(line)
    return EXIT_OK


# --------------------------------------------------------------------------- parser


def register(subparsers, run_map: dict) -> None:
    """Add the onboarding subcommands; ``run_map[name] = handler(args) -> int``."""
    def env_arg(sp):
        sp.add_argument("--env-file", default=None, dest="env_file",
                        help="credentials file (default: <kit>/.env)")

    sp = subparsers.add_parser("creds", help="store/list/check credentials in .env (values never printed)")
    csub = sp.add_subparsers(dest="creds_cmd", metavar="set|list|check")
    csub.required = True
    c = csub.add_parser("set", help="prompt for a value (no echo) and store NAME=value")
    c.add_argument("name", metavar="NAME")
    c.add_argument("--value-from-stdin", action="store_true", dest="value_from_stdin",
                   help="read the value from stdin instead of prompting")
    env_arg(c)
    c = csub.add_parser("list", help="names in .env")
    env_arg(c)
    c = csub.add_parser("check", help="are NAMEs resolvable (environment, .env, op://)?")
    c.add_argument("names", metavar="NAME", nargs="+")
    env_arg(c)
    sp.set_defaults(fn=cmd_creds)
    run_map["creds"] = cmd_creds

    sp = subparsers.add_parser("init-scenario", help="write a scenario scaffold to fill in")
    sp.add_argument("--name", default=None, help='feature name, e.g. "Chat with Manuals"')
    sp.add_argument("--url", default=None, help="app URL, e.g. http://localhost:3000")
    sp.add_argument("--out", dest="scenario_out", default=None, metavar="FILE",
                    help="scenario file to write (default: scenarios/<slug>.json)")
    sp.add_argument("--login", choices=LOGIN_TYPES, default=None, help="login type (default: none)")
    sp.add_argument("--username-env", dest="username_env", default=None, metavar="NAME")
    sp.add_argument("--password-env", dest="password_env", default=None, metavar="NAME")
    sp.add_argument("--login-url", dest="login_url", default=None, help="form login page (default: /login)")
    sp.add_argument("--username-selector", dest="username_selector", default=None)
    sp.add_argument("--password-selector", dest="password_selector", default=None)
    sp.add_argument("--submit-selector", dest="submit_selector", default=None)
    sp.add_argument("--success-selector", dest="success_selector", default=None)
    sp.add_argument("--step", action="append", default=None, metavar='"Title :: plain English"',
                    help="one step per flag, in order")
    sp.add_argument("--interactive", action="store_true", help="ask for missing answers in the terminal")
    sp.add_argument("--force", action="store_true", help="overwrite an existing file")
    sp.set_defaults(fn=cmd_init_scenario)
    run_map["init-scenario"] = cmd_init_scenario

    sp = subparsers.add_parser("validate", help="validate a scenario and list its steps")
    sp.add_argument("scenario", metavar="SCENARIO")
    sp.add_argument("--out", default=None, help=f"where logs/validate.json goes (default: {DEFAULT_OUT})")
    env_arg(sp)
    sp.set_defaults(fn=cmd_validate)
    run_map["validate"] = cmd_validate

    sp = subparsers.add_parser("inspect", help="list a page's inputs/buttons/links with stable selectors")
    sp.add_argument("url", metavar="URL")
    sp.add_argument("--login-from", dest="login_from", default=None, metavar="SCENARIO",
                    help="log in first using this scenario's login block")
    sp.add_argument("--headless", action="store_true")
    sp.add_argument("--json", action="store_true", help="print JSON instead of the table")
    sp.add_argument("--all", action="store_true", help="include hidden elements")
    sp.add_argument("--max", type=int, default=INSPECT_MAX_ROWS, help=f"row cap (default {INSPECT_MAX_ROWS})")
    sp.add_argument("--settle-ms", dest="settle_ms", type=int, default=INSPECT_SETTLE_MS,
                    help="wait after load before reading the DOM")
    sp.add_argument("--out", default=DEFAULT_OUT, help=f"output directory (default: {DEFAULT_OUT})")
    env_arg(sp)
    sp.set_defaults(fn=cmd_inspect)
    run_map["inspect"] = cmd_inspect


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        print(f"error: {message}", file=sys.stderr)
        raise SystemExit(EXIT_BAD_INPUT)


def build_parser() -> tuple[argparse.ArgumentParser, dict]:
    p = _Parser(prog="python -m demo_smoke", description="Onboarding commands of the demo smoke kit.")
    sub = p.add_subparsers(dest="cmd", metavar="<cmd>")
    sub.required = True
    run_map: dict = {}
    register(sub, run_map)
    return p, run_map


def main(argv: list[str] | None = None) -> int:
    parser, run_map = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code or 0)
    try:
        return int(run_map[args.cmd](args))
    except KeyboardInterrupt:
        _err("interrupted")
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
