"""Scenario JSON loading + hand-written validation (no third-party schema lib).

See ``scenarios/schema.json`` for the documented shape.  ``load`` returns the
scenario with defaults applied, an added ``"_dir"`` (Path of the scenario
file's directory) and upload file paths resolved against that directory.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
ACTIONS = ("goto", "click", "fill", "type", "press", "upload", "hover", "scroll",
           "wait", "wait_for", "screenshot")
EXPECTS = ("text", "selector", "url_contains", "not_text")
LOGIN_TYPES = ("none", "form", "basic")
DEFAULT_VIEWPORT = {"width": 1920, "height": 1080}
DEFAULT_MAX_SECONDS = 90
DEFAULT_TIMEOUT_S = 60


class ScenarioError(ValueError):
    """Invalid scenario file (bad input, CLI exit 4)."""


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_str(v, allow_empty: bool = False) -> bool:
    return isinstance(v, str) and (allow_empty or v.strip() != "")


def _validate_action(a, where: str, errors: list[str]) -> None:
    if not isinstance(a, dict) or len(a) != 1:
        errors.append(f"{where} must be an object with exactly one key, one of: {', '.join(ACTIONS)}")
        return
    (name, val), = a.items()
    if name not in ACTIONS:
        errors.append(f"{where} unknown action '{name}' (allowed: {', '.join(ACTIONS)})")
        return
    if name in ("goto", "click", "hover", "press", "screenshot"):
        if not _is_str(val):
            errors.append(f"{where}.{name} must be a non-empty string")
        elif name == "screenshot" and not re.match(r"^[A-Za-z0-9._-]+$", val):
            errors.append(f"{where}.screenshot name must be a plain file name")
    elif name == "fill":
        if not isinstance(val, dict):
            errors.append(f"{where}.fill must be {{selector, text}}")
        else:
            if not _is_str(val.get("selector")):
                errors.append(f"{where}.fill.selector must be a non-empty string")
            if not isinstance(val.get("text"), str):
                errors.append(f"{where}.fill.text must be a string")
    elif name == "type":
        if not isinstance(val, dict):
            errors.append(f"{where}.type must be {{selector, text, delay_ms}}")
        else:
            if not _is_str(val.get("selector")):
                errors.append(f"{where}.type.selector must be a non-empty string")
            if not isinstance(val.get("text"), str):
                errors.append(f"{where}.type.text must be a string")
            if "delay_ms" in val and not (_is_num(val["delay_ms"]) and val["delay_ms"] >= 0):
                errors.append(f"{where}.type.delay_ms must be a number >= 0")
    elif name == "upload":
        if not isinstance(val, dict):
            errors.append(f"{where}.upload must be {{selector, files: [...]}}")
        else:
            if not _is_str(val.get("selector")):
                errors.append(f"{where}.upload.selector must be a non-empty string")
            files = val.get("files")
            if not isinstance(files, list) or not files or not all(_is_str(f) for f in files):
                errors.append(f"{where}.upload.files must be a non-empty list of paths")
    elif name == "scroll":
        if not isinstance(val, dict) or not (("selector" in val) ^ ("y" in val)):
            errors.append(f"{where}.scroll must be {{selector}} or {{y}}")
        elif "selector" in val and not _is_str(val["selector"]):
            errors.append(f"{where}.scroll.selector must be a non-empty string")
        elif "y" in val and not _is_num(val["y"]):
            errors.append(f"{where}.scroll.y must be a number")
    elif name == "wait":
        if not isinstance(val, dict) or not _is_num(val.get("ms")) or val["ms"] < 0:
            errors.append(f"{where}.wait must be {{ms: number >= 0}}")
    elif name == "wait_for":
        if not isinstance(val, dict) or not (("selector" in val) ^ ("text" in val)):
            errors.append(f"{where}.wait_for must be {{selector}} or {{text}} (+ optional timeout_s)")
        else:
            key = "selector" if "selector" in val else "text"
            if not _is_str(val[key]):
                errors.append(f"{where}.wait_for.{key} must be a non-empty string")
            if "timeout_s" in val and not (_is_num(val["timeout_s"]) and val["timeout_s"] > 0):
                errors.append(f"{where}.wait_for.timeout_s must be a positive number")
            for extra in val:
                if extra not in (key, "timeout_s"):
                    errors.append(f"{where}.wait_for has unknown key '{extra}'")


def _validate_expect(e, where: str, errors: list[str]) -> None:
    if not isinstance(e, dict) or not e:
        errors.append(f"{where} must be an object with one of: {', '.join(EXPECTS)}")
        return
    kinds = [k for k in EXPECTS if k in e]
    if len(kinds) != 1:
        errors.append(f"{where} must contain exactly one of: {', '.join(EXPECTS)}")
        return
    kind = kinds[0]
    if not _is_str(e[kind]):
        errors.append(f"{where}.{kind} must be a non-empty string")
    allowed = {"selector": {"contains": lambda v: isinstance(v, str),
                            "count_min": lambda v: _is_num(v) and v >= 0}}.get(kind, {})
    for key in e:
        if key != kind and key not in allowed:
            errors.append(f"{where} has unknown key '{key}' for expect '{kind}'")
    for key, ok in allowed.items():
        if key in e and not ok(e[key]):
            errors.append(f"{where}.{key} has the wrong type")


def _validate_login(login, errors: list[str]) -> None:
    if not isinstance(login, dict):
        errors.append("login must be an object")
        return
    t = login.get("type")
    if t not in LOGIN_TYPES:
        errors.append(f"login.type must be one of: {', '.join(LOGIN_TYPES)}")
        return
    if t == "form":
        for key in ("username_selector", "password_selector", "submit_selector",
                    "username_env", "password_env"):
            if not _is_str(login.get(key)):
                errors.append(f"login.{key} is required for login.type 'form'")
        for key in ("url", "success_selector"):
            if key in login and not _is_str(login[key]):
                errors.append(f"login.{key} must be a non-empty string")
    elif t == "basic":
        for key in ("username_env", "password_env"):
            if not _is_str(login.get(key)):
                errors.append(f"login.{key} is required for login.type 'basic'")


def validate(data: dict) -> list[str]:
    """Return a list of human-readable problems; ``[]`` means valid."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["scenario must be a JSON object"]
    if not _is_str(data.get("name")):
        errors.append("name must be a non-empty string")
    if "slug" in data and not (isinstance(data["slug"], str)
                               and re.match(r"^[a-z0-9][a-z0-9-]*$", data["slug"])):
        errors.append("slug must match ^[a-z0-9][a-z0-9-]*$ (lowercase, digits, dashes)")
    url = data.get("app_url")
    if not _is_str(url) or not re.match(r"^https?://\S+$", url):
        errors.append("app_url must be an http(s) URL, e.g. http://localhost:3000")
    if "viewport" in data:
        vp = data["viewport"]
        if not isinstance(vp, dict) or not all(
            isinstance(vp.get(k), int) and not isinstance(vp.get(k), bool) and vp.get(k) > 0
            for k in ("width", "height")
        ):
            errors.append("viewport must be {width: int > 0, height: int > 0}")
    if "login" in data:
        _validate_login(data["login"], errors)
    if "max_length_seconds" in data and not (
        _is_num(data["max_length_seconds"]) and data["max_length_seconds"] > 0
    ):
        errors.append("max_length_seconds must be a positive number")
    for key in ("intro", "outro"):
        if key in data and not isinstance(data[key], str):
            errors.append(f"{key} must be a string")
    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        errors.append("steps must be a non-empty list")
        return errors
    seen: set[str] = set()
    for i, step in enumerate(steps):
        where = f"steps[{i}]"
        if not isinstance(step, dict):
            errors.append(f"{where} must be an object")
            continue
        sid = step.get("id")
        if not _is_str(sid) or not ID_RE.match(sid):
            errors.append(f"{where}.id must match ^[a-z0-9][a-z0-9_-]*$")
        elif sid in seen:
            errors.append(f"{where}.id '{sid}' is duplicated")
        else:
            seen.add(sid)
            where = f"steps[{i}] ({sid})"
        if not _is_str(step.get("title")):
            errors.append(f"{where}.title must be a non-empty string")
        if "narration" in step and not isinstance(step["narration"], str):
            errors.append(f"{where}.narration must be a string")
        actions = step.get("actions")
        if not isinstance(actions, list):
            errors.append(f"{where}.actions must be a list")
        else:
            for j, a in enumerate(actions):
                _validate_action(a, f"{where}.actions[{j}]", errors)
        expect = step.get("expect", [])
        if not isinstance(expect, list):
            errors.append(f"{where}.expect must be a list")
        else:
            for j, e in enumerate(expect):
                _validate_expect(e, f"{where}.expect[{j}]", errors)
        if "timeout_s" in step and not (_is_num(step["timeout_s"]) and step["timeout_s"] > 0):
            errors.append(f"{where}.timeout_s must be a positive number")
        for key in step:
            if key not in ("id", "title", "narration", "actions", "expect", "timeout_s"):
                errors.append(f"{where} has unknown key '{key}'")
    return errors


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "demo"


def load(path: str | Path, check_files: bool = False) -> dict:
    """Load, validate, apply defaults, resolve relative upload paths.

    Raises ``ScenarioError`` with a one-line message on any problem.  With
    ``check_files=True`` missing upload files are also an error.
    """
    p = Path(path)
    if not p.is_file():
        raise ScenarioError(f"scenario file not found: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ScenarioError(f"{p}: invalid JSON at line {e.lineno} column {e.colno}: {e.msg}") from None
    except OSError as e:
        raise ScenarioError(f"{p}: cannot read: {e}") from None
    errors = validate(data)
    if errors:
        raise ScenarioError(f"{p.name}: " + "; ".join(errors))
    base = p.resolve().parent
    data["_dir"] = base
    data["_path"] = str(p.resolve())
    data.setdefault("slug", slugify(data["name"]))
    data.setdefault("viewport", dict(DEFAULT_VIEWPORT))
    data.setdefault("login", {"type": "none"})
    data.setdefault("max_length_seconds", DEFAULT_MAX_SECONDS)
    data.setdefault("intro", "")
    data.setdefault("outro", "")
    missing: list[str] = []
    for step in data["steps"]:
        step.setdefault("timeout_s", DEFAULT_TIMEOUT_S)
        step.setdefault("expect", [])
        step.setdefault("narration", "")
        for action in step["actions"]:
            if "upload" in action:
                resolved = []
                for f in action["upload"]["files"]:
                    fp = Path(f)
                    if not fp.is_absolute():
                        fp = base / fp
                    resolved.append(str(fp))
                    if check_files and not fp.is_file():
                        missing.append(str(fp))
                action["upload"]["files"] = resolved
    if missing:
        raise ScenarioError("upload file(s) not found: " + ", ".join(missing))
    return data


def step_ids(scenario: dict) -> list[str]:
    return [s["id"] for s in scenario.get("steps", [])]
