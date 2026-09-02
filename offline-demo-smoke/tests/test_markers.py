import json

import pytest

from demo_smoke import markers


def _sample():
    m = markers.new(1700000000.5)
    markers.add_step(m, "open", 2.0, 4.5, "PASS", [[2.5, 4.0]])
    markers.add_step(m, "ask", 7, 12, "PASS", [])
    m["outro_t"] = 13.0
    m["end_t"] = 16.0
    return m


def test_new_shape():
    m = markers.new(123.0)
    assert m == {"capture_start_epoch": 123.0, "intro_t": 0.0, "outro_t": 0.0,
                 "end_t": 0.0, "steps": []}


def test_add_step_coerces_types():
    m = _sample()
    assert m["steps"][1] == {"id": "ask", "t_start": 7.0, "t_end": 12.0, "status": "PASS",
                             "wait_windows": []}
    assert m["steps"][0]["wait_windows"] == [[2.5, 4.0]]


def test_save_load_roundtrip(out_dir):
    m = _sample()
    p = markers.save(m, out_dir)
    assert p == out_dir / "logs" / "markers.json"
    assert json.loads(p.read_text()) == m
    assert markers.load(out_dir) == m


def test_load_missing_is_clear(out_dir):
    with pytest.raises(FileNotFoundError, match="record"):
        markers.load(out_dir)


def test_load_invalid_json(out_dir):
    (out_dir / "logs").mkdir(parents=True)
    (out_dir / "logs" / "markers.json").write_text("{nope")
    with pytest.raises(ValueError, match="not valid JSON"):
        markers.load(out_dir)


def test_validate_reports_problems():
    m = _sample()
    m["steps"][0]["t_end"] = 1.0        # end before start
    m["steps"][1]["status"] = "MAYBE"
    m["steps"][1]["wait_windows"] = [[1]]
    del m["end_t"]
    errs = markers.validate(m)
    assert any("t_end < t_start" in e for e in errs)
    assert any("status" in e for e in errs)
    assert any("wait_windows" in e for e in errs)
    assert any("end_t" in e for e in errs)
    assert markers.validate("nope") == ["markers must be a JSON object"]
    assert markers.validate(_sample()) == []


def test_load_rejects_invalid(out_dir):
    m = _sample()
    m["steps"][1]["t_start"] = 1.0   # overlaps previous step
    markers.save(m, out_dir)
    with pytest.raises(ValueError, match="before the previous step"):
        markers.load(out_dir)
