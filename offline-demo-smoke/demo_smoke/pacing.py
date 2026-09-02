"""Pure timing math shared by ``record`` (live) and ``edit`` (post)."""

from __future__ import annotations

DEFAULT_STEP_SECONDS = 3.0


def next_start(prev_t_start: float, prev_t_end: float, prev_duration: float,
               gap: float = 0.3) -> float:
    """Earliest start for a step: after the previous step finished (+gap) and
    after the previous step's narration would have finished playing."""
    return max(float(prev_t_end) + gap, float(prev_t_start) + float(prev_duration))


def plan(step_ids: list[str], durations: dict, step_seconds: dict | None = None,
         gap: float = 0.3, tail: float = 2.0) -> dict:
    """Planned offsets (seconds since capture start).

    The intro plays from t=0 while the first screen is held, so the first step
    starts when the intro ends.  ``step_seconds`` is the estimated on-screen run
    time of each step (from ``dryrun``); default 3.0 s.
    """
    est = step_seconds or {}
    intro = float(durations.get("intro", 0.0) or 0.0)
    starts: dict[str, float] = {}
    t_start = intro
    t_end = intro
    last_id = None
    for sid in step_ids:
        if last_id is not None:
            t_start = next_start(starts[last_id], t_end, float(durations.get(last_id, 0.0) or 0.0), gap)
        starts[sid] = round(t_start, 3)
        t_end = t_start + float(est.get(sid, DEFAULT_STEP_SECONDS))
        last_id = sid
    if last_id is None:
        outro_t = intro
    else:
        outro_t = max(t_end, starts[last_id] + float(durations.get(last_id, 0.0) or 0.0))
    end_t = outro_t + float(durations.get("outro", 0.0) or 0.0) + tail
    return {"intro_t": 0.0, "steps": starts, "outro_t": round(outro_t, 3), "end_t": round(end_t, 3)}
