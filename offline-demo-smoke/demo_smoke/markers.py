"""``logs/markers.json``: actual times observed during the paced ``record`` run.

Schema::

    {"capture_start_epoch": float, "intro_t": 0.0, "outro_t": float, "end_t": float,
     "steps": [{"id", "t_start", "t_end", "status", "wait_windows": [[t0, t1], ...]}]}
"""

from __future__ import annotations

import json
from pathlib import Path

FILENAME = "markers.json"
STATUSES = ("PASS", "FAIL", "SKIPPED")


def new(capture_start_epoch: float) -> dict:
    return {"capture_start_epoch": float(capture_start_epoch), "intro_t": 0.0,
            "outro_t": 0.0, "end_t": 0.0, "steps": []}


def add_step(m: dict, step_id: str, t_start: float, t_end: float, status: str,
             wait_windows: list) -> None:
    m.setdefault("steps", []).append({
        "id": str(step_id),
        "t_start": float(t_start),
        "t_end": float(t_end),
        "status": str(status),
        "wait_windows": [[float(a), float(b)] for a, b in (wait_windows or [])],
    })


def validate(m: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(m, dict):
        return ["markers must be a JSON object"]
    for key in ("capture_start_epoch", "intro_t", "outro_t", "end_t"):
        if not isinstance(m.get(key), (int, float)) or isinstance(m.get(key), bool):
            errors.append(f"markers.{key} must be a number")
    steps = m.get("steps")
    if not isinstance(steps, list):
        return [*errors, "markers.steps must be a list"]
    prev_end = -1.0
    for i, st in enumerate(steps):
        where = f"markers.steps[{i}]"
        if not isinstance(st, dict):
            errors.append(f"{where} must be an object")
            continue
        if not st.get("id"):
            errors.append(f"{where}.id missing")
        for key in ("t_start", "t_end"):
            if not isinstance(st.get(key), (int, float)):
                errors.append(f"{where}.{key} must be a number")
        if isinstance(st.get("t_start"), (int, float)) and isinstance(st.get("t_end"), (int, float)):
            if st["t_end"] < st["t_start"]:
                errors.append(f"{where}: t_end < t_start")
            if st["t_start"] < prev_end:
                errors.append(f"{where}: starts before the previous step ended")
            prev_end = st["t_end"]
        if st.get("status") not in STATUSES:
            errors.append(f"{where}.status must be one of {', '.join(STATUSES)}")
        ww = st.get("wait_windows", [])
        if not isinstance(ww, list) or any(
            not (isinstance(w, (list, tuple)) and len(w) == 2) for w in ww
        ):
            errors.append(f"{where}.wait_windows must be a list of [t0, t1] pairs")
    return errors


def path_for(out: Path) -> Path:
    return Path(out) / "logs" / FILENAME


def save(m: dict, out: Path) -> Path:
    p = path_for(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(m, indent=2), encoding="utf-8")
    return p


def load(out: Path) -> dict:
    p = path_for(out)
    if not p.is_file():
        raise FileNotFoundError(f"{p} not found: run `record` first")
    try:
        m = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"{p} is not valid JSON: {e}") from None
    errors = validate(m)
    if errors:
        raise ValueError(f"{p} invalid: " + "; ".join(errors))
    return m
