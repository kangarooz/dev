from itertools import pairwise

import pytest

from demo_smoke import pacing


def test_next_start_waits_for_narration_or_step_end():
    assert pacing.next_start(0.0, 1.0, 5.0) == 5.0        # narration still playing
    assert pacing.next_start(0.0, 6.0, 5.0) == 6.3        # step ran long: end + gap
    assert pacing.next_start(2.0, 2.5, 1.0, gap=1.0) == 3.5


def test_plan_matches_contract_numbers():
    durations = {"intro": 2.0, "a": 4.0, "b": 1.0, "c": 5.0, "outro": 3.0}
    est = {"a": 1.0, "b": 6.0}
    p = pacing.plan(["a", "b", "c"], durations, est)
    assert p["intro_t"] == 0.0
    assert p["steps"] == {"a": 2.0, "b": 6.0, "c": 12.3}
    # c starts 12.3, runs default 3.0 -> ends 15.3; its narration ends 17.3
    assert p["outro_t"] == pytest.approx(17.3)
    assert p["end_t"] == pytest.approx(17.3 + 3.0 + 2.0)
    assert set(p) == {"intro_t", "steps", "outro_t", "end_t"}


def test_plan_first_step_starts_after_intro():
    p = pacing.plan(["x"], {"intro": 4.5, "x": 1.0})
    assert p["steps"]["x"] == 4.5


def test_plan_missing_durations_are_zero():
    p = pacing.plan(["a", "b"], {}, gap=0.5, tail=1.0)
    assert p["steps"] == {"a": 0.0, "b": 3.5}
    assert p["outro_t"] == 6.5
    assert p["end_t"] == 7.5


def test_plan_no_steps():
    p = pacing.plan([], {"intro": 1.0, "outro": 2.0})
    assert p["steps"] == {}
    assert p["outro_t"] == 1.0
    assert p["end_t"] == 5.0


def test_plan_is_monotonic():
    ids = [f"s{i}" for i in range(8)]
    durations = {i: 0.5 + (n % 3) for n, i in enumerate(ids)}
    p = pacing.plan(ids, durations)
    starts = [p["steps"][i] for i in ids]
    assert starts == sorted(starts)
    assert all(b - a >= 0.3 for a, b in pairwise(starts))
