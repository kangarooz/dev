import json

from demo_smoke import report, scenario


def _dry():
    return {"verdict": "FAIL", "attempts": 2,
            "steps": [{"id": "open", "title": "Open | app", "status": "PASS", "expected": "text 'Tiny'",
                       "observed": "found", "screenshot": "logs/step-01-open.png", "seconds": 1.234, "error": None},
                      {"id": "ask", "title": "Ask", "status": "FAIL", "expected": "x", "observed": "y",
                       "screenshot": None, "seconds": 2, "error": "expectation not met"}],
            "console_errors": ["TypeError: boom"],
            "failed_requests": [{"url": "http://x/api", "status": 500, "body_excerpt": "oops"}]}


def _markers():
    return {"capture_start_epoch": 1.0, "intro_t": 0.0, "outro_t": 20.0, "end_t": 25.0,
            "steps": [{"id": "open", "t_start": 3.0, "t_end": 5.0, "status": "PASS", "wait_windows": [[3.5, 4.5]]}]}


def _verify():
    return {"pass": False, "duration": 24.9, "thumbnails": ["final/thumb-10.png"],
            "checks": [{"name": "duration", "pass": True, "detail": "24.9 <= 100"},
                       {"name": "black_frames", "pass": False, "detail": "black at 0.0s"}]}


def test_write_report_and_result(out_dir, simple_scenario_path):
    scen = scenario.load(simple_scenario_path)
    out_dir.mkdir(parents=True)
    (out_dir / "final").mkdir()
    (out_dir / "final" / "tiny-app.mp4").write_bytes(b"0")
    env = {"os": "Linux", "python": "3.11", "ffmpeg": "/x/ffmpeg", "ffmpeg_version": "7.0",
           "ffprobe": None, "chrome": "/x/chrome", "torch_device": "none", "chatterbox": False,
           "hints": ["ffprobe not found"],
           "llm": {"base_url": "http://l/v1", "model": "m", "reachable": True, "tool_call": {"pass": True}}}
    md_path, json_path = report.write(out_dir, scen, _dry(), _markers(), _verify(), env,
                                      "template", "FAIL", error="verification checks failed")
    assert md_path == out_dir / "report.md" and json_path == out_dir / "result.json"
    md = md_path.read_text()
    lines = md.splitlines()
    assert lines[0] == "# Demo smoke report: Tiny App"
    assert "**Verdict: FAIL**" in lines[:4]
    assert "## Error" in md and "verification checks failed" in md
    assert "| 1 | open | Open \\| app | PASS | 1.2 |" in md
    assert "| 2 | ask | Ask | FAIL | 2.0 |" in md and "expectation not met" in md
    assert "TypeError: boom" in md and "http://x/api" in md
    assert "| black_frames | FAIL | black at 0.0s |" in md
    assert "| open | 3.0 | 5.0 | PASS | 3.5-4.5 |" in md
    assert "`final/tiny-app.mp4`" in md and "`report.md`" in md
    assert "| Chrome | /x/chrome |" in md and "tool_call=PASS" in md
    assert "ffprobe not found" in md
    res = json.loads(json_path.read_text())
    assert res["verdict"] == "FAIL" and res["error"] == "verification checks failed"
    assert res["scenario"]["slug"] == "tiny-app" and "_dir" not in res["scenario"]
    assert res["final_video"] == "final/tiny-app.mp4"
    assert res["verify"]["pass"] is False and res["dryrun"]["attempts"] == 2
    assert "final/tiny-app.mp4" in res["artifacts"] and "result.json" in res["artifacts"]


def test_write_report_minimal(out_dir):
    scen = {"name": "Bare", "slug": "bare", "app_url": "http://x"}
    md_path, json_path = report.write(out_dir, scen, None, None, None, {}, "template", "ERROR",
                                      error="doctor failed: ffmpeg missing")
    md = md_path.read_text()
    assert "**Verdict: ERROR**" in md
    assert "No dry run results" in md and "No verification results" in md
    assert "| ffmpeg | missing |" in md
    res = json.loads(json_path.read_text())
    assert res["verdict"] == "ERROR" and res["dryrun"] is None and res["final_video"] is None
