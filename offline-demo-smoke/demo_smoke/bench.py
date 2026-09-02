"""``bench``: run the SAME scenario under several drivers and compare hard numbers.

Drivers (``--driver``, repeatable)::

    template                          the kit's own ``run --narration template`` (no LLM at all)
    llm:<base-url>|<model>            ``run --narration llm`` against an OpenAI-compatible endpoint
    opencode:<provider/model>         the full agent path: ``opencode run --agent demo-smoke --auto
                                      --model <provider/model> --format json --command smoke ...``
    opencode:<provider/model>@<url>   same, with that provider's ``baseURL`` overridden through
                                      ``OPENCODE_CONFIG_CONTENT`` (the tests point it at a fake server)

Every driver run gets ``DIR/runs/<driver-slug>/r<N>/`` - an ordinary kit output directory (the
``template``/``llm`` drivers run ``python -m demo_smoke run`` as a subprocess so nothing leaks between
runs; the ``opencode`` driver runs the real binary with the kit as its working directory).  The bench
never edits the scenario.  After each run :func:`collect` reads the run directory (``logs/run.json``,
``result.json``, ``audio/narration.json``, ``audio/durations.json``, ``logs/verify.json``, ``logs/doctor.json``
and, for the agent path, the saved JSON event stream) into ``bench.json``; :mod:`demo_smoke.bench_report`
merges the runs (plus ``--baseline`` rows) into ``DIR/report.md`` and ``DIR/bench.json``.

``HOME`` is never touched, so a hosted provider uses the user's own OpenCode auth.  Every opencode
driver sets ``OPENCODE_CONFIG_CONTENT`` to at least ``{model, small_model}`` = the driver's model, so
OpenCode's title/summary agents never fall back to the kit's default local ollama model; an
``@<base-url>`` override adds a provider block: a provider the kit's ``opencode.json`` already
defines keeps its SDK and only gets a new ``baseURL`` (plus the model id); any other provider (the
tests' ``fake``) is defined from scratch as ``@ai-sdk/openai-compatible``.

A per-run timeout kills the driver's whole process tree (its own process group / job), not only the
direct child, so a stuck OpenCode run cannot leave Chrome or ffmpeg writing into the run directory.

Exit codes follow the kit: 0 every run PASS, 2 some run FAIL, 3 some run ERROR (or the bench itself
could not run a driver), 4 bad input (driver spec, scenario, baseline file).
"""

from __future__ import annotations

import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import __version__

KIT = Path(__file__).resolve().parents[1]
# last-resort OpenCode location after PATH: an npm-prefix install under the user's home
DEFAULT_OPENCODE = str(Path.home() / ".opencode-bin" / "node_modules" / ".bin" / "opencode")
STAGES = ("doctor", "dryrun", "narrate", "synth", "record", "edit", "verify")
TTS_CHOICES = ("auto", "turbo", "nano", "classic", "tone")
PLAYBOOK_MIN_KIT_COMMANDS = 7    # doctor dryrun narrate-validate synth record edit verify
PLAYBOOK_MIN_TOOL_CALLS = 8      # ... plus writing narration.json (narrate-template or the write tool)
DEFAULT_TIMEOUT_S = 3600         # per run (the whole driver process)
DEFAULT_LLM_TIMEOUT_S = 180      # per LLM request inside the llm driver (``run --timeout``)
KILL_DRAIN_S = 10                # seconds to wait for a killed process tree to release its pipes
EXIT_OK, EXIT_FAIL, EXIT_ERROR, EXIT_BAD_INPUT = 0, 2, 3, 4

# Small English stopword list for the on-screen reference metric (tokens that would match
# any narration and say nothing about whether the narrator describes what is on screen).
_STOPWORD_TEXT = """
a about above after again against all also am an and any are as at be because been before being
below between both but by can could did do does doing down during each few for from further get
had has have having he her here hers herself him himself his how i if in into is it its itself
just let me more most my myself no nor not now of off on once only or other our ours ourselves
out over own same she should so some such than that the their theirs them themselves then there
these they this those through to too under until up very was we were what when where which while
who whom why will with would you your yours yourself yourselves
first next then finally now already still yet one two three
"""
STOPWORDS = frozenset(_STOPWORD_TEXT.split())
_TOKEN = re.compile(r"[a-z0-9]+")
_NARRATE_LINE = re.compile(r"^\[narrate\]\s+source=(?P<source>\w+)(?P<rest>.*)$", re.MULTILINE)


class BenchError(ValueError):
    """Bad input to the bench (driver spec, scenario, baseline); exit 4."""


# --------------------------------------------------------------------------- drivers


@dataclass
class Driver:
    kind: str                  # "template" | "llm" | "opencode"
    spec: str                  # the --driver string as given
    model: str | None = None   # llm: model id; opencode: provider/model
    base_url: str | None = None  # llm: endpoint; opencode: optional baseURL override
    slug: str = ""

    @property
    def provider(self) -> str | None:
        if self.kind == "opencode" and self.model:
            return self.model.split("/", 1)[0]
        return None

    @property
    def model_id(self) -> str | None:
        """The model part after ``provider/`` (opencode) or the model itself (llm)."""
        if self.kind == "opencode" and self.model and "/" in self.model:
            return self.model.split("/", 1)[1]
        return self.model


def slugify(text: str, limit: int = 60) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", str(text)).strip("-.").lower()
    return (s or "x")[:limit].rstrip("-.")


def parse_driver(spec: str) -> Driver:
    """``template`` | ``llm:<base-url>|<model>`` | ``opencode:<provider/model>[@<base-url>]``."""
    s = (spec or "").strip()
    if not s:
        raise BenchError("empty --driver")
    kind, _, rest = s.partition(":")
    kind = kind.strip().lower()
    if kind == "template":
        if rest.strip():
            raise BenchError(f"driver 'template' takes no argument: {spec!r}")
        return Driver("template", s, slug="template")
    if kind == "llm":
        base, sep, model = rest.rpartition("|")
        if not sep or "://" not in base or not model.strip():
            raise BenchError(f"llm driver must be 'llm:<base-url>|<model>' (got {spec!r})")
        base, model = base.strip(), model.strip()
        return Driver("llm", s, model=model, base_url=base, slug="llm-" + slugify(model))
    if kind == "opencode":
        model, base = rest.strip(), None
        at = model.rfind("@")
        if at != -1 and "://" in model[at + 1:]:
            model, base = model[:at].strip(), model[at + 1:].strip()
        if "/" not in model or not model.split("/", 1)[0] or not model.split("/", 1)[1]:
            raise BenchError(f"opencode driver must be 'opencode:<provider/model>[@<base-url>]' (got {spec!r})")
        return Driver("opencode", s, model=model, base_url=base, slug="opencode-" + slugify(model))
    raise BenchError(f"unknown driver kind {kind!r} in {spec!r} (template | llm:<url>|<model> | "
                     "opencode:<provider/model>[@<url>])")


def parse_drivers(specs: list[str]) -> list[Driver]:
    """Parse every spec; make slugs unique (``-2``, ``-3`` ...) so runs never share a directory."""
    drivers = [parse_driver(s) for s in (specs or ["template"])]
    seen: dict[str, int] = {}
    for d in drivers:
        n = seen.get(d.slug, 0) + 1
        seen[d.slug] = n
        if n > 1:
            d.slug = f"{d.slug}-{n}"
    return drivers


def run_dir(out: Path, driver: Driver | str, n: int) -> Path:
    """``DIR/runs/<driver-slug>/r<N>``."""
    slug = driver.slug if isinstance(driver, Driver) else str(driver)
    return Path(out) / "runs" / slug / f"r{int(n)}"


# --------------------------------------------------------------------------- small helpers


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path) -> dict | list | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_text(path: Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def tokens(text: str) -> set[str]:
    """Lower-case word tokens (>= 3 chars) minus stopwords."""
    return {t for t in _TOKEN.findall(str(text or "").lower()) if len(t) >= 3 and t not in STOPWORDS}


def on_screen_tokens(scenario: dict) -> set[str]:
    """Tokens from every ``expect`` ``text``/``contains`` value and every step title."""
    found: set[str] = set()
    for step in scenario.get("steps", []) or []:
        found |= tokens(step.get("title", ""))
        for e in step.get("expect", []) or []:
            if not isinstance(e, dict):
                continue
            for key in ("text", "contains"):
                if e.get(key):
                    found |= tokens(e[key])
    return found


def narration_segments(narr: dict | None) -> list[tuple[str, str]]:
    """``[(id, text)]`` in spoken order: intro, steps, outro (missing pieces skipped)."""
    if not isinstance(narr, dict):
        return []
    segs: list[tuple[str, str]] = []
    if isinstance(narr.get("intro"), str):
        segs.append(("intro", narr["intro"]))
    for st in narr.get("steps") or []:
        if isinstance(st, dict) and isinstance(st.get("text"), str):
            segs.append((str(st.get("id")), st["text"]))
    if isinstance(narr.get("outro"), str):
        segs.append(("outro", narr["outro"]))
    return segs


def references_on_screen(narr: dict | None, scenario: dict) -> float | None:
    """Fraction of narration segments sharing a (non-stopword) token with the on-screen strings."""
    segs = narration_segments(narr)
    if not segs:
        return None
    screen = on_screen_tokens(scenario)
    hits = sum(1 for _, text in segs if tokens(text) & screen)
    return round(hits / len(segs), 3)


def _words(text: str) -> int:
    from .narration import words
    return words(text)


def narration_metrics(narr: dict | None, scenario: dict) -> dict:
    """Words per segment, totals, speaking-time estimate and the on-screen reference fraction."""
    from .narration import WORDS_PER_SECOND

    segs = narration_segments(narr)
    per = {sid: _words(text) for sid, text in segs}
    total = sum(per.values())
    return {
        "segments": len(segs),
        "words_per_segment": per,
        "total_words": total,
        "estimated_seconds": round(total / WORDS_PER_SECOND, 1) if segs else None,
        "references_on_screen": references_on_screen(narr, scenario),
    }


def _narrate_note(stdout: str) -> tuple[str | None, str]:
    m = _NARRATE_LINE.search(stdout or "")
    if not m:
        return None, ""
    return m.group("source"), m.group("rest").strip()


def llm_metrics(stdout: str, run_log: dict | None) -> dict:
    """The llm driver's narration attempts, from the structured ``llm`` block ``run`` writes into
    ``logs/run.json`` (``attempts``, ``problems`` per rejected attempt, ``fallback_reason``).

    ``stdout`` only supplies the ``[narrate]`` note for the record; nothing is inferred from it.
    """
    source, note = _narrate_note(stdout)
    if run_log and run_log.get("narration_source"):
        source = run_log.get("narration_source")
    block = (run_log or {}).get("llm") if isinstance((run_log or {}).get("llm"), dict) else {}
    attempts = block.get("attempts")
    problems = block.get("problems") if isinstance(block.get("problems"), list) else []
    fallback_reason = block.get("fallback_reason")
    fallback = bool(block["fallback"]) if "fallback" in block else source == "template"
    return {"attempts": int(attempts) if isinstance(attempts, int) and not isinstance(attempts, bool) else 0,
            "problems": [str(x) for x in problems], "fallback": fallback,
            "fallback_reason": str(fallback_reason) if fallback_reason else None, "note": note or None}


def _failing_step(steps: list | None) -> str | None:
    for st in steps or []:
        if isinstance(st, dict) and st.get("status") == "FAIL":
            return str(st.get("id"))
    return None


def _checks(verify: dict | None) -> list[dict]:
    return [{"name": c.get("name"), "pass": bool(c.get("pass"))}
            for c in ((verify or {}).get("checks") or []) if isinstance(c, dict)]


def _rel(path: Path | None, base: Path) -> str | None:
    if path is None:
        return None
    try:
        return Path(path).relative_to(base).as_posix()
    except ValueError:
        return str(path)


# --------------------------------------------------------------------------- collect


def collect(run_out: Path, driver: Driver, scenario: dict, n: int = 1, started: str | None = None,
            finished: str | None = None, wall_s: float | None = None, exit_code: int | None = None,
            stdout: str | None = None, events: dict | None = None, error: str | None = None,
            argv: list[str] | None = None) -> dict:
    """Read one run directory into the per-run ``bench.json`` dict (pure I/O, never raises on gaps).

    ``events`` is the parsed OpenCode summary for the agent driver (``opencode_events.summary``
    output, or the fallback dict from :func:`opencode_summary`); ``stdout`` the driver's captured
    stdout (used for the ``llm`` driver's narrate note).
    """
    run_out = Path(run_out)
    logs = run_out / "logs"
    run_log = _read_json(logs / "run.json")
    result = _read_json(run_out / "result.json")
    dry = _read_json(logs / "dryrun.json")
    verify = _read_json(logs / "verify.json")
    doctor = _read_json(logs / "doctor.json")
    narr = _read_json(run_out / "audio" / "narration.json")
    durations = _read_json(run_out / "audio" / "durations.json")
    validate_log = _read_json(logs / "narrate-validate.json")
    markers = _read_json(logs / "markers.json")
    if stdout is None:
        stdout = _read_text(logs / "bench-stdout.txt")
    run_log = run_log if isinstance(run_log, dict) else None
    result = result if isinstance(result, dict) else None
    dry = dry if isinstance(dry, dict) else None
    verify = verify if isinstance(verify, dict) else None
    markers = markers if isinstance(markers, dict) else None
    durations = durations if isinstance(durations, dict) else {}

    stages: dict[str, float | None] = {s: None for s in STAGES}
    if driver.kind == "opencode":
        for s, v in ((events or {}).get("stages") or {}).items():
            if s in stages:
                stages[s] = v
    elif run_log and isinstance(run_log.get("timings"), dict):
        for s, v in run_log["timings"].items():
            if s in stages and v is not None:
                stages[s] = float(v)
        if wall_s is None and run_log["timings"].get("total") is not None:
            wall_s = float(run_log["timings"]["total"])

    # verdict / failing stage
    failing_stage: str | None = None
    verdict: str
    if driver.kind == "opencode":
        verdict, failing_stage = _agent_verdict(events, dry, verify, exit_code, error)
    else:
        verdict = (run_log or {}).get("verdict") or (result or {}).get("verdict") or "ERROR"
        if verdict != "PASS":
            failing_stage = (run_log or {}).get("stage")
        if error and verdict == "PASS":
            verdict = "ERROR"
        if error is None and verdict == "ERROR":
            error = (run_log or {}).get("error") or (result or {}).get("error")
    failing_step = _failing_step((dry or {}).get("steps")) or _failing_step((markers or {}).get("steps"))

    # narration
    if driver.kind == "template":
        source = "template"
        retries = 0
        validation_errors = 0
    elif driver.kind == "llm":
        source = (run_log or {}).get("narration_source") or _narrate_note(stdout)[0] or ("llm" if narr else None)
        llm = llm_metrics(stdout, run_log)
        retries = max(0, llm["attempts"] - 1) if llm["attempts"] else 0
        validation_errors = len(llm["problems"])
    else:
        ev = events or {}
        if ev.get("narration_written_by_agent"):
            source = "agent"
        elif ev.get("used_narrate_template"):
            source = "template"
        elif ev.get("used_narrate_llm"):
            source = "llm"
        else:
            source = "agent" if narr else None
        validate_calls = sum(1 for c in (ev.get("kit_commands") or []) if c == "narrate-validate")
        retries = max(0, validate_calls - 1)
        validation_errors = len((validate_log or {}).get("errors") or []) if isinstance(validate_log, dict) else 0
    narration = {"source": source, "validation_errors": validation_errors, "retries": retries,
                 "written": narr is not None, **narration_metrics(narr, scenario)}

    seg_seconds = {k: float(v) for k, v in durations.items() if isinstance(v, (int, float))}
    audio = {"total_seconds": round(sum(seg_seconds.values()), 2) if seg_seconds else None,
             "segments": seg_seconds}
    final_video = None
    for p in sorted((run_out / "final").glob("*.mp4")) if (run_out / "final").is_dir() else []:
        final_video = p
        break
    video = {"duration": (verify or {}).get("duration"), "verify_pass": (verify or {}).get("pass"),
             "checks": _checks(verify), "path": _rel(final_video, run_out)}

    rec: dict = {
        "driver": driver.spec, "kind": driver.kind, "model": driver.model, "base_url": driver.base_url,
        "slug": driver.slug, "run": int(n), "out": str(run_out),
        "started": started, "finished": finished, "wall_s": round(wall_s, 1) if wall_s is not None else None,
        "stages": stages, "verdict": verdict, "exit_code": exit_code,
        "failing_stage": failing_stage, "failing_step": failing_step, "error": error,
        "narration": narration, "audio": audio, "video": video,
        "opencode": _agent_block(events) if driver.kind == "opencode" else None,
        "llm": llm_metrics(stdout, run_log) if driver.kind == "llm" else None,
        "env": doctor if isinstance(doctor, dict) else None,
        "report": _rel(run_out / "report.md", run_out) if (run_out / "report.md").is_file() else None,
        "argv": argv,
    }
    return rec


def _agent_block(events: dict | None) -> dict:
    ev = events or {}
    return {
        "tool_calls": ev.get("tool_calls") or 0,
        "kit_tool_calls": len(ev.get("kit_commands") or []),
        "chained_kit_calls": ev.get("chained_kit_calls") or 0,
        "commands": ev.get("commands") or [],
        "kit_commands": ev.get("kit_commands") or [],
        "assistant_messages": ev.get("assistant_messages") or 0,
        "permission_prompts": ev.get("permission_prompts") or 0,
        "denied": ev.get("denied") or 0,
        "steps": ev.get("steps") or 0,
        "steps_limit": ev.get("steps_limit"),
        "step_limit_reached": bool(ev.get("step_limit_reached")),
        "tokens_in": ev.get("tokens_in"), "tokens_out": ev.get("tokens_out"),
        "tokens_total": ev.get("tokens_total"), "cost": ev.get("cost"),
        "narration_written_by_agent": bool(ev.get("narration_written_by_agent")),
        "used_narrate_template": bool(ev.get("used_narrate_template")),
        "final_status": ev.get("final_status"),
        "session_id": ev.get("session_id"),
        "failed_tool_calls": ev.get("failed_tool_calls") or [],
        "errors": ev.get("errors") or [],
        "parser": ev.get("parser", "opencode_events"),
    }


def _agent_verdict(events: dict | None, dry: dict | None, verify: dict | None, exit_code: int | None,
                   error: str | None) -> tuple[str, str | None]:
    """PASS when verify passed (whatever else the session reported - an ``error`` event from the
    provider, say for title generation, does not undo a delivered video; the message stays in
    ``error`` and the report notes it); FAIL when a kit command reported a feature failure; else ERROR.

    A run the bench had to kill on its timeout is ERROR even when verify had passed: its wall
    time is the timeout, not a measurement, and must never enter a PASS mean (the template/llm
    drivers are treated the same way in :func:`collect`)."""
    ev = events or {}
    last = (ev.get("kit_commands") or ["opencode"])[-1]
    if exit_code is None and error and error.startswith("timed out"):
        return "ERROR", last
    if verify and verify.get("pass") is True and (dry or {}).get("verdict", "PASS") == "PASS":
        return "PASS", None
    if error:
        return "ERROR", "opencode"
    if dry and dry.get("verdict") == "FAIL":
        return "FAIL", "dryrun"
    for c in ev.get("failed_tool_calls") or []:
        if c.get("exit_code") == 2:
            from .opencode_events import kit_command_of
            return "FAIL", kit_command_of(c.get("command")) or "unknown"
    if verify and verify.get("pass") is False:
        return "FAIL", "verify"
    if ev.get("final_status") == "error" or (exit_code not in (0, None)):
        return "ERROR", last
    return "ERROR", last if ev.get("kit_commands") else "opencode"


# --------------------------------------------------------------------------- opencode event stream


def opencode_summary(stdout: str) -> dict:
    """``opencode_events.summary(parse(stdout))``; a plain-text fallback when the module is missing."""
    try:
        oe = importlib.import_module("demo_smoke.opencode_events")
    except ImportError:
        oe = None
    if oe is not None:
        summ = oe.summary(oe.parse(stdout))
        summ["parser"] = "opencode_events"
        return summ
    commands: list[str] = []
    for line in (stdout or "").splitlines():
        if "-m demo_smoke" in line:
            m = re.search(r"-m demo_smoke ([A-Za-z][\w-]*)", line)
            commands.append(m.group(1) if m else "unknown")
    return {"parser": "fallback", "tool_calls": len(commands), "commands": [], "kit_commands": commands,
            "failed_tool_calls": [], "assistant_messages": 0, "permission_prompts": 0, "denied": 0,
            "steps": 0, "step_limit_reached": False, "tokens_in": None, "tokens_out": None,
            "tokens_total": None, "cost": None, "errors": [], "wall_s": None,
            "stages": {s: None for s in STAGES}, "narration_written_by_agent": False,
            "used_narrate_template": "narrate-template" in commands,
            "used_narrate_llm": "narrate-llm" in commands, "final_status": None, "session_id": None}


def _agent_steps_limit() -> int | None:
    """``steps:`` from the demo-smoke agent's frontmatter (the limit the agent runs under)."""
    text = _read_text(KIT / ".opencode" / "agents" / "demo-smoke.md")
    m = re.search(r"^steps:\s*(\d+)\s*$", text, re.MULTILINE)
    return int(m.group(1)) if m else None


# --------------------------------------------------------------------------- running drivers


def find_opencode(explicit: str | None = None) -> str | None:
    """``--opencode-bin`` -> ``OPENCODE_BIN`` -> ``opencode`` on PATH -> ``~/.opencode-bin/.../opencode``."""
    for cand in (explicit, os.environ.get("OPENCODE_BIN")):
        if cand:
            return cand
    on_path = shutil.which("opencode")
    if on_path:
        return on_path
    return DEFAULT_OPENCODE if Path(DEFAULT_OPENCODE).is_file() else None


def _base_env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(KIT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    # the agent runs bare `python -m demo_smoke ...`: make that the interpreter running the bench (a venv's
    # bin/ or Scripts/ directory is exactly the executable's parent), even when the venv was never activated
    env["PATH"] = str(Path(sys.executable).parent) + (os.pathsep + env["PATH"] if env.get("PATH") else "")
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")     # the kit's stdout is read back as UTF-8 (Windows: not cp1252)
    try:
        from .env import find_chrome
        found = find_chrome()
    except Exception:  # noqa: BLE001 - discovery is best effort here
        found = None
    if found and "DEMO_SMOKE_CHROME" not in env:
        env["DEMO_SMOKE_CHROME"] = found
    loopback = "127.0.0.1,localhost"
    for key in ("NO_PROXY", "no_proxy"):
        env[key] = loopback + ("," + env[key] if env.get(key) else "")
    return env


def kit_run_argv(driver: Driver, scenario: Path, run_out: Path, tts: str, headless: bool,
                 ref: Path | None, llm_timeout: int, python: str | None = None) -> list[str]:
    """``python -m demo_smoke run ...`` for the template/llm drivers.  ``llm_timeout`` is the per-request
    LLM timeout (``run --timeout``), not the bench's per-run timeout: the kit's template fallback must
    still get its chance before the bench kills the run."""
    argv = [python or sys.executable, "-m", "demo_smoke", "run", str(scenario), "--out", str(run_out),
            "--tts", tts, "--capture", "screencast",
            "--narration", "llm" if driver.kind == "llm" else "template"]
    if headless:
        argv.append("--headless")
    if ref is not None:
        argv += ["--ref", str(ref)]
    if driver.kind == "llm":
        argv += ["--base-url", driver.base_url or "", "--model", driver.model or "",
                 "--timeout", str(int(llm_timeout))]
    return argv


def _quote_arg(value) -> str:
    """One argument of the ``--command smoke`` message.  OpenCode tokenises the message on whitespace
    unless the token is double-quoted (quotes are stripped on substitution), so a path with a space
    (``C:\\Users\\First Last\\...``) must be quoted or ``$1``/``$2`` land in the wrong places."""
    return '"' + str(value).replace('"', "") + '"'


def opencode_argv(opencode_bin: str, driver: Driver, scenario: Path, run_out: Path, headless: bool,
                  ref: Path | None, tts: str | None = None) -> list[str]:
    """The ``opencode run ... --command smoke "<scenario> <out> [tts:<backend>] [headless] [<ref>]"`` argv.
    ``tts`` is forwarded as a ``tts:<backend>`` token the smoke command file turns into ``synth --tts``."""
    parts = [_quote_arg(scenario), _quote_arg(run_out)]
    if tts:
        parts.append(f"tts:{tts}")
    if headless:
        parts.append("headless")
    if ref:
        parts.append(_quote_arg(ref))
    message = " ".join(parts)
    return [opencode_bin, "run", "--agent", "demo-smoke", "--auto", "--model", driver.model or "",
            "--format", "json", "--command", "smoke", message]


def opencode_config_override(driver: Driver, kit_config: dict | None = None) -> dict | None:
    """``OPENCODE_CONFIG_CONTENT`` for an ``@<base-url>`` driver; ``None`` without an override."""
    if driver.kind != "opencode" or not driver.base_url:
        return None
    provider, model_id = driver.provider or "", driver.model_id or ""
    if kit_config is None:
        kit_config = _read_json(KIT / "opencode.json") or {}
    known = provider in ((kit_config.get("provider") or {}) if isinstance(kit_config, dict) else {})
    block: dict = {"options": {"baseURL": driver.base_url}, "models": {model_id: {"name": model_id}}}
    if not known:
        block = {"npm": "@ai-sdk/openai-compatible", "name": f"{provider} (bench override)", **block}
    return {"provider": {provider: block}, "model": driver.model, "small_model": driver.model}


def opencode_config_content(driver: Driver, kit_config: dict | None = None) -> dict:
    """``OPENCODE_CONFIG_CONTENT`` for any opencode driver: the provider override for ``@<base-url>``
    drivers, otherwise just ``{model, small_model}`` so title/summary generation uses the driver's own
    model instead of the kit's default (a local ollama that is not necessarily running)."""
    override = opencode_config_override(driver, kit_config)
    if override is not None:
        return override
    return {"model": driver.model, "small_model": driver.model}


def opencode_env(driver: Driver, timeout: int) -> dict:
    env = _base_env()
    env["OPENCODE_DISABLE_MODELS_FETCH"] = "1"
    env["OPENCODE_DISABLE_AUTOUPDATE"] = "1"
    env["OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS"] = str(int(timeout) * 1000)
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(opencode_config_content(driver))
    return env


def _popen_tree_kwargs() -> dict:
    """``Popen`` options that put the child (and everything it spawns) into its own group."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def kill_process_tree(proc: subprocess.Popen) -> None:
    """Kill ``proc`` and every descendant: the process group on POSIX (``start_new_session``),
    ``taskkill /T /F`` on Windows.  Never raises (a leader that already exited still names its group)."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, check=False,
                           timeout=30)
        else:
            import signal
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception as e:  # noqa: BLE001 - best effort; the direct child is killed below anyway
        print(f"[bench] could not kill the process tree of pid {proc.pid}: {e}", flush=True)
    try:
        proc.kill()
    except Exception as e:  # noqa: BLE001 - already gone
        print(f"[bench] could not kill pid {proc.pid}: {e}", flush=True)


def _drain(proc: subprocess.Popen) -> tuple[str, str]:
    """Collect what a killed process wrote; give up after ``KILL_DRAIN_S`` if an orphan still holds the pipes."""
    try:
        out, err = proc.communicate(timeout=KILL_DRAIN_S)
    except subprocess.TimeoutExpired as e:
        out, err = _bytes(e.stdout), _bytes(e.stderr)
    except (OSError, ValueError):
        out, err = "", ""
    return out or "", err or ""


def _run_process(argv: list[str], env: dict, cwd: Path, timeout: int, logs: Path) -> tuple[int | None, str, str, str | None]:
    """Run ``argv`` in its own process group; return (exit_code, stdout, stderr, error).

    On timeout (or Ctrl-C) the whole tree is killed - OpenCode's bash tool, the kit's python,
    Chrome and ffmpeg - before the partial output is collected.  Output is saved under ``logs/``
    and decoded as UTF-8 whatever the locale (the kit and OpenCode both emit UTF-8).
    """
    logs.mkdir(parents=True, exist_ok=True)
    error: str | None = None
    proc: subprocess.Popen | None = None
    try:
        proc = subprocess.Popen(argv, cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                **_popen_tree_kwargs())
        out, err = proc.communicate(timeout=timeout)
        code, out, err = proc.returncode, out or "", err or ""
    except subprocess.TimeoutExpired:
        kill_process_tree(proc)
        out, err = _drain(proc)
        code = None
        error = f"timed out after {timeout} s"
    except OSError as e:
        code, out, err = None, "", ""
        error = f"could not start {argv[0]}: {e}"
    except BaseException:
        if proc is not None:                     # Ctrl-C: the child is in its own group, so kill it ourselves
            kill_process_tree(proc)
            _drain(proc)
        raise
    (logs / "bench-stdout.txt").write_text(out, encoding="utf-8")
    (logs / "bench-stderr.txt").write_text(err, encoding="utf-8")
    return code, out, err, error


def _bytes(v) -> str:
    if v is None:
        return ""
    return v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)


def run_driver(driver: Driver, n: int, scenario_path: Path, scenario: dict, out: Path, *, tts: str,
               headless: bool, ref: Path | None, timeout: int, opencode_bin: str | None,
               python: str | None = None, llm_timeout: int = DEFAULT_LLM_TIMEOUT_S) -> dict:
    """Run one driver once into ``run_dir(out, driver, n)`` and return its collected ``bench.json``.

    ``timeout`` bounds the whole driver process; ``llm_timeout`` is the llm driver's per-request timeout.
    """
    dest = run_dir(out, driver, n)
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    logs = dest / "logs"
    started, t0 = _now_iso(), time.time()
    events: dict | None = None
    if driver.kind == "opencode":
        if not opencode_bin:
            code, stdout, error, argv = None, "", "opencode binary not found (set OPENCODE_BIN or --opencode-bin)", []
        else:
            argv = opencode_argv(opencode_bin, driver, scenario_path, dest, headless, ref, tts=tts)
            code, stdout, _, error = _run_process(argv, opencode_env(driver, timeout), KIT, timeout, logs)
            events = opencode_summary(stdout)
            events["steps_limit"] = _agent_steps_limit()
            if error is None and code not in (0, None):
                error = f"opencode exited with code {code}"
                tail = [ln for ln in _read_text(logs / "bench-stderr.txt").splitlines() if ln.strip()][-2:]
                if tail:
                    error += ": " + " | ".join(tail)
            if error is None and events.get("errors"):
                error = "; ".join(str(e) for e in events["errors"][:3])
            (logs / "opencode-events.json").write_text(json.dumps(events, indent=2, default=str), encoding="utf-8")
    else:
        argv = kit_run_argv(driver, scenario_path, dest, tts, headless, ref, llm_timeout, python)
        code, stdout, _, error = _run_process(argv, _base_env(), KIT, timeout, logs)
        if error is None and code not in (0, 2) and not (logs / "run.json").is_file():
            error = f"run exited with code {code} without a report"
    wall = time.time() - t0
    rec = collect(dest, driver, scenario, n=n, started=started, finished=_now_iso(), wall_s=wall,
                  exit_code=code, stdout=stdout, events=events, error=error, argv=argv)
    (dest / "bench.json").write_text(json.dumps(rec, indent=2, default=str), encoding="utf-8")
    return rec


# --------------------------------------------------------------------------- meta hooks


def _meta_module():
    """``demo_smoke.bench_meta`` (whole-display recording + meta narration/video) when it is installed."""
    try:
        return importlib.import_module("demo_smoke.bench_meta")
    except ImportError:
        return None


def meta_view(bench: dict) -> dict:
    """The bench dict the way ``bench_meta`` reads it: ``rows`` (one per driver, manual rows merged)
    and a ``scenario`` object; the ``drivers`` spec list is left out so it cannot shadow ``rows``."""
    from .bench_report import baseline_rows

    scen = bench.get("scenario")
    nested = scen if isinstance(scen, dict) else {}       # already a view (or a hand-written file)
    return {"scenario": {"name": bench.get("name") or nested.get("name"), "slug": bench.get("slug") or nested.get("slug"),
                         "path": nested.get("path") if nested else scen},
            "rows": list(bench.get("rows") or []) + baseline_rows(bench.get("baseline") or []),
            "runs": bench.get("runs") or [], "started": bench.get("started"), "finished": bench.get("finished")}


def start_screen_recording(meta, out: Path, index: int, driver: Driver, n: int, fps: int = 15):
    """``DisplayCapture`` for ``meta/clips/NN-<slug>-rN.mp4`` (started); ``None`` when it cannot start."""
    clip = meta.meta_clip_path(out, index, driver.slug, n)
    clip.parent.mkdir(parents=True, exist_ok=True)
    cap = meta.DisplayCapture(clip, fps=fps)
    try:
        cap.start()
    except Exception as e:  # noqa: BLE001 - no display (CI) must not stop the bench
        print(f"[bench] screen recording unavailable: {e}", flush=True)
        return None
    return cap


def build_meta(meta, out: Path, bench: dict, clips: list[str], tts: str, ref: Path | None) -> Path:
    """Meta narration from the bench numbers + the meta MP4 from ``clips`` (``bench_meta`` does the work)."""
    from . import tts as tts_mod

    tts_mod.set_offline_env(False)
    view = meta_view(bench)
    narration = meta.meta_narration(view, bench.get("baseline") or None)
    target = meta.meta_output_path(out, view)
    return meta.build_meta_video([Path(c) for c in clips], narration, target, tts=tts, ref=ref)


# --------------------------------------------------------------------------- command


def cmd_bench(args) -> int:
    from . import bench_report
    from . import scenario as scenario_mod

    out = Path(args.out).resolve()      # the drivers run with cwd=KIT, so --out must not stay relative
    try:
        drivers = parse_drivers(list(args.driver or []))
    except BenchError as e:
        print(f"error: {e}", file=sys.stderr, flush=True)
        return EXIT_BAD_INPUT
    if int(args.repeat) < 1:
        print("error: --repeat must be >= 1", file=sys.stderr, flush=True)
        return EXIT_BAD_INPUT
    scenario_path = Path(args.scenario).resolve()
    try:
        scen = scenario_mod.load(scenario_path, check_files=True)
    except scenario_mod.ScenarioError as e:
        print(f"error: {e}", file=sys.stderr, flush=True)
        return EXIT_BAD_INPUT
    ref = Path(args.ref).resolve() if args.ref else None
    if ref is not None and not ref.is_file():
        print(f"error: reference voice not found: {ref}", file=sys.stderr, flush=True)
        return EXIT_BAD_INPUT
    baseline: list[dict] = []
    if args.baseline:
        try:
            baseline = bench_report.load_baseline(args.baseline)
        except (OSError, ValueError, TypeError) as e:
            print(f"error: baseline: {e}", file=sys.stderr, flush=True)
            return EXIT_BAD_INPUT
    opencode_bin = find_opencode(args.opencode_bin) if any(d.kind == "opencode" for d in drivers) else None
    meta = _meta_module() if (args.record_screen or args.meta_narrate or args.meta_from_clips) else None
    if (args.record_screen or args.meta_narrate or args.meta_from_clips) and meta is None:
        print("bench: note: demo_smoke.bench_meta is not available; --record-screen/--meta-narrate ignored",
              flush=True)
    if args.meta_narrate and not (args.record_screen or args.meta_from_clips):
        print("error: --meta-narrate needs --record-screen (or --meta-from-clips)", file=sys.stderr, flush=True)
        return EXIT_BAD_INPUT

    out.mkdir(parents=True, exist_ok=True)
    (out / "logs").mkdir(exist_ok=True)
    started, t0 = _now_iso(), time.time()
    runs: list[dict] = []
    screen_recordings: list[str] = []
    print(f"bench: {scen.get('name')} -> {out} drivers={[d.spec for d in drivers]} repeat={args.repeat}",
          flush=True)
    interrupted = False
    index = 0
    try:
        for d in drivers:
            for n in range(1, int(args.repeat) + 1):
                index += 1
                cap = None
                if meta is not None and args.record_screen:
                    cap = start_screen_recording(meta, out, index, d, n)
                print(f"[bench] {d.slug} r{n}: running ...", flush=True)
                try:
                    rec = run_driver(d, n, scenario_path, scen, out, tts=args.tts, headless=args.headless, ref=ref,
                                     timeout=int(args.timeout_s), opencode_bin=opencode_bin,
                                     llm_timeout=int(getattr(args, "llm_timeout", DEFAULT_LLM_TIMEOUT_S)))
                except BaseException:
                    if cap is not None:
                        cap.abort()
                    raise
                if cap is not None:
                    try:
                        clip = cap.stop()
                        screen_recordings.append(str(clip))
                        rec["screen_recording"] = str(clip)
                    except Exception as e:  # noqa: BLE001 - a failed meta capture must not lose the run
                        rec["screen_recording_error"] = str(e)
                        print(f"[bench] screen recording failed: {e}", flush=True)
                runs.append(rec)
                mins = (rec["wall_s"] or 0) / 60.0
                print(f"[bench] {d.slug} r{n}: {rec['verdict']} {mins:.1f} min narration={rec['narration']['source']}"
                      + (f" tool_calls={rec['opencode']['tool_calls']}" if rec.get("opencode") else "")
                      + (f" ({rec['error']})" if rec.get("error") else ""), flush=True)
    except KeyboardInterrupt:
        interrupted = True

    bench = {
        "version": __version__,
        "scenario": str(scenario_path), "name": scen.get("name"), "slug": scen.get("slug"),
        "started": started, "finished": _now_iso(), "wall_s": round(time.time() - t0, 1),
        "out": str(out), "drivers": [asdict(d) for d in drivers], "repeat": int(args.repeat),
        "args": {"tts": args.tts, "headless": bool(args.headless), "ref": str(ref) if ref else None,
                 "timeout_s": int(args.timeout_s), "opencode_bin": opencode_bin,
                 "llm_timeout": int(getattr(args, "llm_timeout", DEFAULT_LLM_TIMEOUT_S)),
                 "record_screen": bool(args.record_screen), "meta_narrate": bool(args.meta_narrate)},
        "baseline_file": str(args.baseline) if args.baseline else None,
        "baseline": baseline, "runs": runs, "screen_recordings": screen_recordings,
        "interrupted": interrupted,
    }
    bench["rows"] = bench_report.aggregate(runs)
    bench["differences"] = bench_report.differences(bench["rows"], runs, baseline)
    clips = [str(Path(c).resolve()) for c in (args.meta_from_clips or [])] or screen_recordings
    if meta is not None and (args.meta_narrate or args.meta_from_clips):
        if not clips:
            bench["meta_error"] = "no screen recordings were captured"
            print("[bench] meta: no screen recordings were captured; meta video skipped", flush=True)
        else:
            try:
                meta_path = build_meta(meta, out, bench, clips, args.tts, ref)
                bench["meta_video"] = str(meta_path)
                print(f"[bench] meta -> {meta_path}", flush=True)
            except Exception as e:  # noqa: BLE001 - the report is still worth writing
                bench["meta_error"] = str(e)
                print(f"[bench] meta: error: {e}", flush=True)
    report_path, json_path = bench_report.write(out, bench)
    (out / "logs" / "bench.json").write_text(json.dumps(bench, indent=2, default=str), encoding="utf-8")
    verdicts = [r["verdict"] for r in runs]
    overall = "PASS" if verdicts and all(v == "PASS" for v in verdicts) else (
        "ERROR" if (not verdicts or "ERROR" in verdicts or interrupted) else "FAIL")
    print(f"bench: {overall} {len(runs)} run(s) " + " ".join(f"{r['slug']}/r{r['run']}={r['verdict']}" for r in runs)
          + f" -> {report_path}, {json_path}", flush=True)
    if interrupted:
        return 130
    return {"PASS": EXIT_OK, "FAIL": EXIT_FAIL}.get(overall, EXIT_ERROR)


def register(subparsers, run_map: dict) -> None:
    """Add ``bench`` to an argparse subparsers object; fill ``run_map``."""
    sp = subparsers.add_parser("bench", help="run one scenario under several drivers and compare the numbers")
    sp.add_argument("scenario", metavar="SCENARIO", help="scenario JSON (read only; the bench never edits it)")
    sp.add_argument("--out", required=True, help="bench directory (runs/<driver>/r<N>/, report.md, bench.json)")
    sp.add_argument("--driver", action="append", default=None, metavar="SPEC",
                    help="template | llm:<base-url>|<model> | opencode:<provider/model>[@<base-url>] "
                         "(repeatable; default: template)")
    sp.add_argument("--tts", choices=TTS_CHOICES, default="auto",
                    help="TTS backend for every run (`run --tts` for template/llm; a `tts:<backend>` token in "
                         "the smoke command for an OpenCode agent, whose doctor-says-tone rule still applies)")
    sp.add_argument("--ref", default=None, help="reference voice WAV passed to every run")
    sp.add_argument("--headless", action="store_true", help="headless Chrome for dryrun/record")
    sp.add_argument("--repeat", type=int, default=1, help="runs per driver (default 1)")
    sp.add_argument("--record-screen", action="store_true",
                    help="capture the whole display while each driver runs, one clip per run (needs a visible "
                         "desktop and ffmpeg gdigrab/avfoundation/x11grab; not under --headless in a container)")
    sp.add_argument("--meta-narrate", action="store_true",
                    help="build meta/<slug>-bench.mp4 from the screen recordings (needs --record-screen)")
    sp.add_argument("--meta-from-clips", nargs="+", default=None, metavar="MP4", help=__import__("argparse").SUPPRESS)
    sp.add_argument("--baseline", default=None, help="manual entries JSON merged into the report (see bench/)")
    sp.add_argument("--opencode-bin", default=None, help="OpenCode binary (default: OPENCODE_BIN, then PATH)")
    sp.add_argument("--timeout-s", type=int, default=DEFAULT_TIMEOUT_S,
                    help="per-run timeout in seconds; the whole driver process tree is killed (default %(default)s)")
    sp.add_argument("--llm-timeout", type=int, default=DEFAULT_LLM_TIMEOUT_S,
                    help="llm driver: per-request LLM timeout passed as `run --timeout` (default %(default)s)")
    sp.set_defaults(fn=cmd_bench)
    run_map["bench"] = cmd_bench


def main(argv: list[str] | None = None) -> int:
    """Standalone entry: ``python -m demo_smoke.bench SCENARIO --out DIR --driver ...``."""
    import argparse

    p = argparse.ArgumentParser(prog="python -m demo_smoke.bench")
    sub = p.add_subparsers(dest="cmd")
    sub.required = True
    register(sub, {})
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] != "bench":
        args = ["bench", *args]
    try:
        ns = p.parse_args(args)
    except SystemExit as e:
        # argparse exits 2 on a usage error, which is the bench's "some run FAIL" code: report bad input.
        return EXIT_BAD_INPUT if e.code == 2 else int(e.code or 0)
    return int(ns.fn(ns))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
