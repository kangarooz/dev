"""End-to-end: ``python -m demo_smoke run`` against the static fixture app.

Runs the real CLI in a subprocess (headless Chromium, screencast capture, the
``tone`` TTS backend and template narration) and checks the artifacts the
pipeline promises: ``final/<slug>.mp4``, ``result.json`` (verdict PASS, all
verify checks passing), ``report.md`` and the three thumbnails.  A second run
with a wrong expectation must exit 2 and name the failing step.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from demo_smoke import chrome
from tests.fixtures.serve import serve_dir

KIT = Path(__file__).resolve().parents[1]
APP_DIR = KIT / "tests" / "fixtures" / "app"
SCEN_DIR = KIT / "tests" / "fixtures" / "scenarios"
FILES_DIR = KIT / "tests" / "fixtures" / "files"
TIMEOUT_S = 600


def _env() -> dict:
    env = dict(os.environ)
    found = chrome.find_chrome()
    if "DEMO_SMOKE_CHROME" not in env and found:
        env["DEMO_SMOKE_CHROME"] = found      # the subprocess uses the same discovery; pin it anyway
    env["PYTHONPATH"] = str(KIT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def _need_chrome() -> None:
    if not chrome.find_chrome():
        pytest.skip("no Chrome binary available (set DEMO_SMOKE_CHROME)")


def _write_scenario(name: str, base_url: str, dest: Path) -> Path:
    """Copy a fixture scenario next to ``dest`` with the served URL and absolute upload paths."""
    data = json.loads((SCEN_DIR / name).read_text(encoding="utf-8"))
    data["app_url"] = base_url
    for step in data["steps"]:
        for action in step.get("actions", []):
            upload = action.get("upload")
            if upload:
                upload["files"] = [str((SCEN_DIR / f).resolve()) for f in upload["files"]]
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return dest


def _run(scenario: Path, out: Path) -> tuple[subprocess.CompletedProcess, float]:
    argv = [sys.executable, "-m", "demo_smoke", "run", str(scenario), "--out", str(out),
            "--tts", "tone", "--capture", "screencast", "--narration", "template", "--headless"]
    t0 = time.monotonic()
    proc = subprocess.run(argv, cwd=str(KIT), env=_env(), capture_output=True, text=True,
                          timeout=TIMEOUT_S, check=False)
    return proc, time.monotonic() - t0


def test_run_pass_end_to_end(tmp_path):
    _need_chrome()
    out = tmp_path / "out"
    with serve_dir(APP_DIR) as base:
        scenario = _write_scenario("fixture-pass.json", base, tmp_path / "scenario" / "pass.json")
        proc, seconds = _run(scenario, out)
    detail = f"exit={proc.returncode}\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    assert proc.returncode == 0, detail
    assert "run: PASS" in proc.stdout, detail
    for stage in ("doctor", "dryrun", "narrate", "synth", "record", "edit", "verify"):
        assert f"[{stage}]" in proc.stdout, detail

    videos = sorted((out / "final").glob("*.mp4"))
    assert videos == [out / "final" / "fixture-pass.mp4"], videos
    assert videos[0].stat().st_size > 10_000
    for pct in (10, 50, 90):
        thumb = out / "final" / f"thumb-{pct}.png"
        assert thumb.is_file() and thumb.stat().st_size > 1000, thumb
    assert (out / "report.md").is_file()
    report = (out / "report.md").read_text(encoding="utf-8")
    assert "**Verdict: PASS**" in report
    assert "fixture-pass.mp4" in report

    result = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert result["verdict"] == "PASS"
    assert result["error"] is None
    assert result["final_video"] == "final/fixture-pass.mp4"
    assert result["narration_source"] == "template"
    assert [s["status"] for s in result["dryrun"]["steps"]] == ["PASS", "PASS", "PASS"]
    assert [s["status"] for s in result["markers"]["steps"]] == ["PASS", "PASS", "PASS"]

    verify = result["verify"]
    assert verify["pass"] is True
    names = [c["name"] for c in verify["checks"]]
    for name in ("duration", "av_length_match", "no_black_start", "no_black_end", "narration_audible"):
        assert name in names, names
    failed = [c for c in verify["checks"] if not c["pass"]]
    assert failed == [], failed
    assert verify == json.loads((out / "logs" / "verify.json").read_text(encoding="utf-8"))
    assert verify["duration"] <= result["scenario"]["max_length_seconds"] + 10

    # the tone narration must actually be on the audio bus
    audible = next(c for c in verify["checks"] if c["name"] == "narration_audible")
    assert audible["pass"], audible
    # the screencast filled the whole viewport (no padded band)
    frames = json.loads((out / "raw" / "frames.json").read_text(encoding="utf-8"))
    assert set(frames["frame_sizes"]) == {"1280x720"}, frames["frame_sizes"]
    assert frames["note"] == "", frames["note"]
    # per-command logs and the exact ffmpeg command are on disk
    for log in ("doctor", "dryrun", "record", "edit", "verify", "run", "markers"):
        assert (out / "logs" / f"{log}.json").is_file(), log
    edit = json.loads((out / "logs" / "edit.json").read_text(encoding="utf-8"))
    assert edit["ok"] is True and edit["argv"][-1].endswith("fixture-pass.mp4")
    assert seconds < TIMEOUT_S


def test_run_fail_scenario_exits_2(tmp_path):
    _need_chrome()
    out = tmp_path / "out"
    with serve_dir(APP_DIR) as base:
        scenario = _write_scenario("fixture-fail.json", base, tmp_path / "scenario" / "fail.json")
        proc, _ = _run(scenario, out)
    detail = f"exit={proc.returncode}\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    assert proc.returncode == 2, detail
    assert "run: FAIL" in proc.stdout, detail

    result = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert result["verdict"] == "FAIL"
    assert result["final_video"] is None
    statuses = {s["id"]: s["status"] for s in result["dryrun"]["steps"]}
    assert statuses == {"open": "PASS", "ask": "FAIL", "never": "SKIPPED"}, statuses
    failing = next(s for s in result["dryrun"]["steps"] if s["status"] == "FAIL")
    assert failing["id"] == "ask" and failing["error"], failing
    assert any("No manuals uploaded" in e for e in result["dryrun"]["console_errors"])

    report = (out / "report.md").read_text(encoding="utf-8")
    assert "**Verdict: FAIL**" in report
    assert "| ask |" in report and "FAIL" in report
    assert (out / "logs" / "smoke-results.md").is_file()
    assert not list((out / "final").glob("*.mp4"))
