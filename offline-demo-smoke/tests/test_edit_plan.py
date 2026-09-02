"""Pure timeline maths in ``edit.plan_timeline``: no ffmpeg involved."""

from __future__ import annotations

import json

import pytest

from demo_smoke import edit

DUR = {"intro": 2.0, "open": 1.0, "ask": 1.5, "outro": 1.0}


def _markers(open_windows=None, ask_windows=None, open_start=2.0):
    return {
        "capture_start_epoch": 1.0, "intro_t": 0.0, "outro_t": 12.0, "end_t": 13.0,
        "steps": [
            {"id": "open", "t_start": open_start, "t_end": 8.0, "status": "PASS",
             "wait_windows": open_windows or []},
            {"id": "ask", "t_start": 8.5, "t_end": 11.0, "status": "PASS",
             "wait_windows": ask_windows or []},
        ],
    }


def _audio_t(plan, sid):
    return next(a["t"] for a in plan["audio"] if a["id"] == sid)


def test_no_wait_windows_is_identity():
    plan = edit.plan_timeline(_markers(), DUR, tail=0.0)
    assert plan["video_segments"] == [
        {"src_start": 0.0, "src_end": 13.0, "speed": 1.0, "out_start": 0.0, "out_end": 13.0}]
    assert plan["total"] == 13.0
    assert [a["id"] for a in plan["audio"]] == ["intro", "open", "ask", "outro"]
    for a in plan["audio"]:
        assert a["t"] == a["src_t"]
    assert _audio_t(plan, "intro") == 0.0
    assert _audio_t(plan, "outro") == 12.0
    assert plan["map"]["speedups"] == []


def test_default_tail_extends_the_picture_after_the_outro():
    plan = edit.plan_timeline(_markers(), DUR)
    assert plan["total"] == pytest.approx(13.0 + edit.DEFAULT_TAIL)


def test_long_window_after_narration_is_sped_up_and_later_audio_shifts_earlier():
    # "open" narration: 2.0 -> 3.0 ; window 4.0 -> 7.0 (3 s) starts after it ended
    plan = edit.plan_timeline(_markers(open_windows=[[4.0, 7.0]]), DUR, min_wait=1.5, speed=4.0,
                              tail=0.0)
    segs = plan["video_segments"]
    assert [(s["src_start"], s["src_end"], s["speed"]) for s in segs] == [
        (0.0, 4.0, 1.0), (4.0, 7.0, 4.0), (7.0, 13.0, 1.0)]
    saved = 3.0 - 3.0 / 4.0
    assert plan["total"] == pytest.approx(13.0 - saved)
    assert plan["map"]["saved_seconds"] == pytest.approx(saved)
    # audio before the window is untouched, audio after it moves earlier by `saved`
    assert _audio_t(plan, "intro") == 0.0
    assert _audio_t(plan, "open") == 2.0
    assert _audio_t(plan, "ask") == pytest.approx(8.5 - saved)
    assert _audio_t(plan, "outro") == pytest.approx(12.0 - saved)
    assert plan["map"]["speedups"][0]["step_id"] == "open"


def test_window_overlapping_own_narration_is_untouched():
    # narration 2.0 -> 3.0 ; window 2.5 -> 7.0 starts while it is still playing
    plan = edit.plan_timeline(_markers(open_windows=[[2.5, 7.0]]), DUR, tail=0.0)
    assert len(plan["video_segments"]) == 1
    assert plan["video_segments"][0]["speed"] == 1.0
    assert plan["total"] == 13.0
    assert _audio_t(plan, "ask") == 8.5


def test_window_overlapping_intro_narration_is_untouched():
    # step starts at 1.0 while the 2 s intro is still playing; window 1.2 -> 5.0
    m = _markers(open_windows=[[1.2, 5.0]], open_start=1.0)
    plan = edit.plan_timeline(m, {**DUR, "open": 0.0}, tail=0.0)
    assert plan["map"]["speedups"] == []
    assert plan["total"] == 13.0


def test_windows_shorter_than_min_wait_are_ignored():
    plan = edit.plan_timeline(_markers(open_windows=[[4.0, 5.4]], ask_windows=[[10.0, 10.5]]),
                              DUR, min_wait=1.5, tail=0.0)
    assert plan["map"]["speedups"] == []
    assert plan["total"] == 13.0
    # exactly min_wait long counts
    plan = edit.plan_timeline(_markers(open_windows=[[4.0, 5.5]]), DUR, min_wait=1.5, tail=0.0)
    assert len(plan["map"]["speedups"]) == 1


def test_multiple_windows_and_custom_speed():
    m = _markers(open_windows=[[3.5, 6.0]], ask_windows=[[10.0, 11.0]])   # ask narr 8.5 -> 10.0
    plan = edit.plan_timeline(m, DUR, min_wait=1.0, speed=2.0, tail=0.0)
    speeds = [s["speed"] for s in plan["video_segments"]]
    assert speeds == [1.0, 2.0, 1.0, 2.0, 1.0]
    assert plan["total"] == pytest.approx(13.0 - 2.5 / 2 - 1.0 / 2)
    assert _audio_t(plan, "ask") == pytest.approx(8.5 - 1.25)
    assert _audio_t(plan, "outro") == pytest.approx(12.0 - 1.25 - 0.5)


def test_windows_are_clamped_to_the_timeline():
    m = _markers(ask_windows=[[10.5, 20.0]])           # runs past end_t
    plan = edit.plan_timeline(m, DUR, tail=0.0)
    assert plan["video_segments"][-1]["src_end"] == 13.0
    assert plan["video_segments"][-1]["speed"] == 4.0
    assert plan["total"] == pytest.approx(10.5 + 2.5 / 4)


def test_remap_is_monotonic_and_exact_at_boundaries():
    plan = edit.plan_timeline(_markers(open_windows=[[4.0, 7.0]]), DUR, tail=0.0)
    segs = plan["video_segments"]
    assert edit.remap(segs, 4.0) == 4.0
    assert edit.remap(segs, 7.0) == pytest.approx(4.75)
    assert edit.remap(segs, 5.0) == pytest.approx(4.25)
    prev = -1.0
    for t in [x / 10 for x in range(131)]:
        cur = edit.remap(segs, t)
        assert cur >= prev
        prev = cur


def test_plan_is_json_serialisable_and_tolerates_missing_durations():
    m = _markers(open_windows=[[4.0, 7.0]])
    plan = edit.plan_timeline(m, {"intro": 2.0}, tail=0.0)    # no step durations known
    text = json.dumps(plan)
    assert "video_segments" in text and "audio" in text and "total" in text and "map" in text
    assert next(a for a in plan["audio"] if a["id"] == "ask")["duration"] == 0.0


def test_empty_markers_yield_a_single_zero_length_plan():
    plan = edit.plan_timeline({"intro_t": 0.0, "outro_t": 0.0, "end_t": 0.0, "steps": []}, {},
                              tail=0.0)
    assert plan["total"] == 0.0
    assert len(plan["video_segments"]) == 1
    assert [a["id"] for a in plan["audio"]] == ["intro", "outro"]
