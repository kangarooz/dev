"""Tolerant parser for ``opencode run --format json`` output (one JSON event per line).

Shape observed with OpenCode 1.18.26 (``tests/fixtures/opencode-events.sample.jsonl``,
captured by ``tests/opencode_capture_events.py``) and confirmed in the binary's
``run`` command: every line is ``{"type", "timestamp", "sessionID", ...extra}`` where

- ``tool_use``     ``part = {tool, callID, state: {status: completed|error, input, output,
                   error?, metadata: {exit?, output?, truncated?}, title, time: {start, end}}}``
- ``step_start``   ``part = {messageID, snapshot, ...}``; one per agentic step (LLM turn)
- ``step_finish``  ``part = {reason: "tool-calls"|"stop"|..., tokens: {input, output, reasoning,
                   total?, cache: {read, write}}, cost}``
- ``text``         ``part = {text, time}``  (assistant message, only once finished)
- ``reasoning``    ``part = {text, ...}``   (only with ``--thinking``)
- ``error``        ``{error: {name, data: {message}}}`` (session error or a rejected command)

Permission prompts are *not* JSON events: with ``--auto`` they are approved silently,
otherwise the binary prints ``permission requested: bash (...); auto-rejecting`` as a
plain line.  A denied tool call shows up as a ``tool_use`` with ``state.status ==
"error"`` and a "rejected permission" style message.  When the agent's ``steps``
limit is hit the model is told "The maximum number of steps allowed for this task
has been reached" and answers with text only.

``parse`` never raises on any input: unknown event types are counted and listed,
non-JSON lines are counted (and scanned for the permission line), missing fields
become ``None``.  Everything else in the kit that needs numbers from an OpenCode run
(the bench driver) goes through ``parse`` + ``summary``.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

KNOWN_EVENT_TYPES = ("tool_use", "step_start", "step_finish", "text", "reasoning", "error")
STAGES = ("doctor", "dryrun", "narrate", "synth", "record", "edit", "verify")
_NARRATE_CMDS = {"narrate-template", "narrate-llm", "narrate-validate"}
STEP_LIMIT_TEXT = "maximum number of steps allowed for this task has been reached"
_DENIED_MARKERS = ("rejected permission", "permissionrejected", "permission denied", "denied",
                   "specified a rule", "user dismissed", "not allowed", "auto-rejecting")
_PERMISSION_LINE = re.compile(r"permission requested:\s*(?P<permission>[^\s(]+)\s*(?:\((?P<patterns>[^)]*)\))?"
                              r"(?P<rest>.*)", re.IGNORECASE)
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_KIT_CMD = re.compile(r"-m\s+demo_smoke\s+([A-Za-z][\w-]*)")


# ----------------------------------------------------------------------------- input


def _iter_lines(lines: Any) -> Iterable[str]:
    if lines is None:
        return []
    if isinstance(lines, bytes):
        lines = lines.decode("utf-8", "replace")
    if isinstance(lines, str):
        return lines.splitlines()
    if isinstance(lines, Path):
        try:
            return lines.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
    return (ln.decode("utf-8", "replace") if isinstance(ln, bytes) else str(ln) for ln in lines)


def _decode(line: str) -> Any:
    """JSON value of a line, or ``None`` when it is not JSON (an ``{``/``[`` prefix is required)."""
    s = line.strip()
    if not s or s[0] not in "{[":
        return None
    try:
        return json.loads(s)
    except (json.JSONDecodeError, ValueError, RecursionError):
        return None


def _events_of(value: Any) -> list[Any]:
    """A decoded line is one event or (if the binary printed an array) several."""
    if isinstance(value, list):
        return value
    return [value]


def _get(d: Any, *keys: str, default: Any = None) -> Any:
    """Nested dict lookup that never raises (``_get(e, "part", "state", "input")``)."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _int_or_none(v: Any) -> int | None:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        try:
            return int(float(v))
        except ValueError:
            return None
    return None


def _num(v: Any) -> float:
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return 0.0
    return 0.0


def _text(v: Any, limit: int | None = None) -> str | None:
    if v is None:
        return None
    s = v if isinstance(v, str) else json.dumps(v, default=str)
    if limit is not None and len(s) > limit:
        return s[-limit:]
    return s


def _looks_denied(msg: str | None) -> bool:
    if not msg:
        return False
    low = msg.lower()
    return any(m in low for m in _DENIED_MARKERS)


# ----------------------------------------------------------------------------- events


def _tool_call(event: dict, index: int) -> dict:
    part = _get(event, "part", default={})
    state = _get(part, "state", default={})
    if not isinstance(state, dict):
        state = {}
    inp = state.get("input")
    if isinstance(inp, str):           # some providers send the arguments as a JSON string
        try:
            inp = json.loads(inp)
        except (json.JSONDecodeError, ValueError):
            inp = {"raw": inp}
    if not isinstance(inp, dict):
        inp = {}
    time = state.get("time") if isinstance(state.get("time"), dict) else {}
    meta = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
    started = _int_or_none(time.get("start"))
    finished = _int_or_none(time.get("end"))
    if started is None:
        started = _int_or_none(event.get("timestamp"))
    error = _text(state.get("error"))
    command = inp.get("command")
    if command is not None and not isinstance(command, str):
        command = _text(command)
    call: dict[str, Any] = {
        "index": index,
        "name": _text(part.get("tool") if isinstance(part, dict) else None) or "unknown",
        "command": command,
        "started": started,
        "finished": finished,
        "seconds": (round((finished - started) / 1000.0, 3)
                    if started is not None and finished is not None and finished >= started else None),
        "status": _text(state.get("status")) or "unknown",
        "call_id": _text(part.get("callID") if isinstance(part, dict) else None),
        "title": _text(state.get("title")),
        "input": inp,
        "error": error,
        "output": _text(state.get("output") if state.get("output") is not None else meta.get("output"), 4000),
        "truncated": bool(meta.get("truncated")) if "truncated" in meta else None,
        "kit_command": kit_command_of(command),
        "denied": _looks_denied(error),
    }
    if "exit" in meta or "exit_code" in meta or "exitCode" in meta:
        code = meta.get("exit", meta.get("exit_code", meta.get("exitCode")))
        call["exit_code"] = _int_or_none(code)
    return call


def kit_command_of(command: str | None) -> str | None:
    """``"dryrun"`` for ``python -m demo_smoke dryrun ...``; ``None`` for anything else."""
    if not command:
        return None
    m = _KIT_CMD.search(command)
    return m.group(1) if m else None


def _permission_from_line(line: str) -> dict | None:
    clean = _ANSI.sub("", line)
    m = _PERMISSION_LINE.search(clean)
    if not m:
        return None
    patterns = [p.strip() for p in (m.group("patterns") or "").split(",") if p.strip()]
    rest = (m.group("rest") or "").lower()
    return {"permission": m.group("permission"), "patterns": patterns,
            "denied": "reject" in rest or "denied" in rest, "raw": clean.strip()[:500]}


def _usage_add(total: dict, tokens: Any, cost: Any) -> bool:
    """Fold one ``step_finish`` into the running usage; ``True`` when it carried numbers."""
    seen = False
    if isinstance(tokens, dict):
        for key in ("input", "output", "reasoning", "total"):
            if key in tokens:
                total[key] = total.get(key, 0) + _num(tokens[key])
                seen = True
        cache = tokens.get("cache")
        if isinstance(cache, dict):
            for key in ("read", "write"):
                if key in cache:
                    total[f"cache_{key}"] = total.get(f"cache_{key}", 0) + _num(cache[key])
                    seen = True
    if cost is not None and not isinstance(cost, (dict, list)):
        total["cost"] = total.get("cost", 0.0) + _num(cost)
        seen = True
    return seen


def _error_of(event: dict) -> dict:
    err = event.get("error")
    if isinstance(err, dict):
        name = _text(err.get("name")) or "Error"
        msg = _get(err, "data", "message")
        if msg is None:
            msg = err.get("message")
        return {"name": name, "message": _text(msg) or name, "timestamp": _int_or_none(event.get("timestamp"))}
    return {"name": "Error", "message": _text(err) or "unknown error",
            "timestamp": _int_or_none(event.get("timestamp"))}


# ----------------------------------------------------------------------------- parse


def parse(lines: Any) -> dict:
    """Parse OpenCode JSON events (a str, bytes, Path or iterable of lines); never raises.

    Returns::

        {"tool_calls": [{"name", "command", "started", "finished", "seconds", "status",
                         "exit_code"?, "call_id", "title", "input", "error", "output",
                         "kit_command", "denied", "index"}],
         "assistant_text": [str], "reasoning": [str],
         "permissions": [{"permission", "patterns", "denied", "raw"}],
         "denied": [{"kind": "tool"|"permission", ...}],
         "usage": {"input", "output", "reasoning", "total", "cache_read", "cache_write",
                   "cost", "steps"} | None,
         "session_id": str | None, "final_status": str,
         "steps": int, "step_limit_reached": bool, "errors": [{"name", "message", "timestamp"}],
         "event_counts": {type: n}, "unknown_event_types": [str],
         "lines": int, "json_lines": int, "non_json_lines": int, "malformed_events": int,
         "first_timestamp": int | None, "last_timestamp": int | None}
    """
    counts: Counter = Counter()
    unknown: list[str] = []
    tool_calls: list[dict] = []
    texts: list[str] = []
    reasoning: list[str] = []
    permissions: list[dict] = []
    errors: list[dict] = []
    usage: dict[str, float] = {}
    usage_seen = False
    session_id: str | None = None
    steps = 0
    finish_reasons: list[str] = []
    step_limit = False
    n_lines = n_json = n_plain = n_malformed = 0
    first_ts: int | None = None
    last_ts: int | None = None

    for raw in _iter_lines(lines):
        if not raw.strip():
            continue
        n_lines += 1
        value = _decode(raw)
        if value is None:
            n_plain += 1
            perm = _permission_from_line(raw)
            if perm is not None:
                permissions.append(perm)
            if STEP_LIMIT_TEXT in raw.lower():
                step_limit = True
            continue
        n_json += 1
        for event in _events_of(value):
            if not isinstance(event, dict):
                n_malformed += 1
                counts["<non-object>"] += 1
                continue
            etype = event.get("type")
            if not isinstance(etype, str) or not etype:
                etype = "<missing>"
            counts[etype] += 1
            ts = _int_or_none(event.get("timestamp"))
            if ts is not None:
                first_ts = ts if first_ts is None else min(first_ts, ts)
                last_ts = ts if last_ts is None else max(last_ts, ts)
            sid = event.get("sessionID") or event.get("session_id") or _get(event, "part", "sessionID")
            if session_id is None and isinstance(sid, str) and sid:
                session_id = sid
            try:
                if etype == "tool_use":
                    tool_calls.append(_tool_call(event, len(tool_calls)))
                elif etype == "step_start":
                    steps += 1
                elif etype == "step_finish":
                    reason = _text(_get(event, "part", "reason"))
                    if reason:
                        finish_reasons.append(reason)
                    if _usage_add(usage, _get(event, "part", "tokens"), _get(event, "part", "cost")):
                        usage_seen = True
                elif etype == "text":
                    t = _get(event, "part", "text")
                    if t is None:
                        t = event.get("text")
                    if isinstance(t, str) and t.strip():
                        texts.append(t)
                        if STEP_LIMIT_TEXT in t.lower():
                            step_limit = True
                elif etype == "reasoning":
                    t = _get(event, "part", "text")
                    if isinstance(t, str) and t.strip():
                        reasoning.append(t)
                elif etype == "error":
                    err = _error_of(event)
                    errors.append(err)
                    if STEP_LIMIT_TEXT in err["message"].lower():
                        step_limit = True
                else:
                    if etype not in unknown:
                        unknown.append(etype)
            except Exception:  # noqa: BLE001 - a weird event must not kill the report
                n_malformed += 1

    denied: list[dict] = [{"kind": "tool", "name": c["name"], "command": c["command"], "error": c["error"],
                           "index": c["index"]} for c in tool_calls if c["denied"]]
    denied += [{"kind": "permission", "permission": p["permission"], "patterns": p["patterns"]}
               for p in permissions if p["denied"]]

    if usage_seen:
        usage_out: dict[str, Any] = {k: (int(v) if float(v).is_integer() and k != "cost" else v)
                                     for k, v in usage.items()}
        if "total" not in usage_out and any(k in usage_out for k in ("input", "output")):
            usage_out["total"] = int(usage_out.get("input", 0) + usage_out.get("output", 0)
                                     + usage_out.get("reasoning", 0))
        usage_out["steps"] = len(finish_reasons) or steps
    else:
        usage_out = None

    return {
        "tool_calls": tool_calls,
        "assistant_text": texts,
        "reasoning": reasoning,
        "permissions": permissions,
        "denied": denied,
        "usage": usage_out,
        "session_id": session_id,
        "final_status": _final_status(counts, finish_reasons, errors, step_limit, texts),
        "steps": steps,
        "step_limit_reached": step_limit,
        "errors": errors,
        "event_counts": dict(counts),
        "unknown_event_types": unknown,
        "lines": n_lines,
        "json_lines": n_json,
        "non_json_lines": n_plain,
        "malformed_events": n_malformed,
        "first_timestamp": first_ts,
        "last_timestamp": last_ts,
    }


def _final_status(counts: Counter, reasons: list[str], errors: list[dict], step_limit: bool,
                  texts: list[str]) -> str:
    """``empty`` | ``error`` | ``step_limit`` | ``completed`` | ``incomplete``."""
    if not counts:
        return "empty"
    if errors:
        return "error"
    if step_limit:
        return "step_limit"
    last = reasons[-1] if reasons else None
    if last in ("stop", "end-turn", "end_turn", "length"):
        return "completed"
    if last is None and texts and not counts.get("tool_use"):
        return "completed"          # text only, no step bookkeeping (older/other emitters)
    return "incomplete"


def parse_file(path: str | Path) -> dict:
    """``parse`` over a saved stdout file (missing/unreadable -> the empty result)."""
    return parse(Path(path))


# ----------------------------------------------------------------------------- derived


def stage_seconds(tool_calls: list[dict]) -> dict[str, float | None]:
    """Wall seconds per kit stage from the tool-call timestamps (``narrate`` = all narrate-* calls).

    A stage the agent never ran is ``None``; repeated calls (retries) are summed.
    """
    out: dict[str, float | None] = {s: None for s in STAGES}
    for c in tool_calls:
        cmd = c.get("kit_command")
        if not cmd or c.get("seconds") is None:
            continue
        stage = "narrate" if cmd in _NARRATE_CMDS else cmd
        if stage not in out:
            continue
        out[stage] = round((out[stage] or 0.0) + float(c["seconds"]), 3)
    return out


def wrote_narration(tool_calls: list[dict]) -> bool:
    """Did the agent write ``audio/narration.json`` itself (edit/write tool, or a shell redirect)?"""
    for c in tool_calls:
        name = (c.get("name") or "").lower()
        inp = c.get("input") if isinstance(c.get("input"), dict) else {}
        target = str(inp.get("filePath") or inp.get("file_path") or inp.get("path") or "")
        if name in ("write", "edit", "patch", "multiedit") and target.replace("\\", "/").endswith("narration.json"):
            return True
        cmd = c.get("command") or ""
        if name == "bash" and "narration.json" in cmd and (">" in cmd or "tee " in cmd or "Set-Content" in cmd):
            return True
    return False


def summary(parsed: dict) -> dict:
    """The numbers the bench report needs, from a ``parse`` result."""
    calls = parsed.get("tool_calls") or []
    commands = [c.get("command") for c in calls if c.get("command")]
    kit_cmds = [c.get("kit_command") for c in calls if c.get("kit_command")]
    failed = [c for c in calls if c.get("status") == "error" or (c.get("exit_code") not in (None, 0))]
    usage = parsed.get("usage") or {}
    first, last = parsed.get("first_timestamp"), parsed.get("last_timestamp")
    return {
        "session_id": parsed.get("session_id"),
        "final_status": parsed.get("final_status"),
        "tool_calls": len(calls),
        "commands": commands,
        "kit_commands": kit_cmds,
        "failed_tool_calls": [{"name": c.get("name"), "command": c.get("command"), "exit_code": c.get("exit_code"),
                               "error": c.get("error")} for c in failed],
        "assistant_messages": len(parsed.get("assistant_text") or []),
        "permission_prompts": len(parsed.get("permissions") or []),
        "denied": len(parsed.get("denied") or []),
        "steps": parsed.get("steps") or 0,
        "step_limit_reached": bool(parsed.get("step_limit_reached")),
        "tokens_in": usage.get("input"),
        "tokens_out": usage.get("output"),
        "tokens_total": usage.get("total"),
        "cost": usage.get("cost"),
        "errors": [e.get("message") for e in parsed.get("errors") or []],
        "unknown_event_types": list(parsed.get("unknown_event_types") or []),
        "wall_s": (round((last - first) / 1000.0, 3) if first is not None and last is not None else None),
        "stages": stage_seconds(calls),
        "narration_written_by_agent": wrote_narration(calls),
        "used_narrate_template": "narrate-template" in kit_cmds,
        "used_narrate_llm": "narrate-llm" in kit_cmds,
    }


# ----------------------------------------------------------------------------- CLI


def cmd_opencode_events(args) -> int:
    """``opencode-events FILE [--out DIR] [--json]``: summarise a saved ``--format json`` stdout."""
    from .env import Paths

    src = Path(args.file)
    if not src.is_file():
        print(f"error: {src} not found", flush=True)
        return 4
    parsed = parse_file(src)
    result = {"file": str(src), "summary": summary(parsed), "events": parsed}
    out = getattr(args, "out", None)
    if out:
        try:
            log = Paths(out).logs / "opencode-events.json"
            log.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        except OSError as e:
            print(f"error: could not write log: {e}", flush=True)
            return 3
    s = result["summary"]
    if getattr(args, "json", False):
        print(json.dumps(result["summary"], indent=2, default=str), flush=True)
    else:
        print(f"opencode-events: {s['final_status']} tool_calls={s['tool_calls']} steps={s['steps']} "
              f"messages={s['assistant_messages']} permissions={s['permission_prompts']} denied={s['denied']} "
              f"tokens={s['tokens_total']} unknown_types={len(s['unknown_event_types'])}", flush=True)
    return 0


def register(subparsers, run_map: dict) -> None:
    """Add ``opencode-events`` to an argparse subparsers object; fill ``run_map``."""
    sp = subparsers.add_parser("opencode-events", help="summarise saved `opencode run --format json` output")
    sp.add_argument("file", metavar="EVENTS.jsonl")
    sp.add_argument("--out", default=None, help="output directory for logs/opencode-events.json (optional)")
    sp.add_argument("--json", action="store_true", help="print the summary as JSON")
    sp.set_defaults(fn=cmd_opencode_events)
    run_map["opencode-events"] = cmd_opencode_events
