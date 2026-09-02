"""``demo_smoke.bench_meta``: display-capture argv per OS, start/stop plumbing with a fake
ffmpeg, the spoken meta narration, the pure placement plan, ``concat_videos`` and the
meta video built from two lavfi testsrc clips (tone TTS)."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from demo_smoke import bench_meta as bm
from demo_smoke import bench_report, ffmpeg_concat
from demo_smoke import ffmpeg_util as ff
from demo_smoke.capture import CaptureError

KIT = Path(__file__).resolve().parents[1]
FAKE_FFMPEG = KIT / "tests" / "fakes" / "ffmpeg_fake.py"


# --------------------------------------------------------------------------- helpers


def fake_ffmpeg(tmp_path: Path) -> str:
    """A launcher for ``tests/fakes/ffmpeg_fake.py`` runnable as an ``ffmpeg`` binary."""
    if os.name == "nt":
        launcher = tmp_path / "ffmpeg.cmd"
        launcher.write_text(f'@"{sys.executable}" "{FAKE_FFMPEG}" %*\r\n', encoding="utf-8")
    else:
        launcher = tmp_path / "ffmpeg"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_FFMPEG}" "$@"\n', encoding="utf-8")
        launcher.chmod(0o755)
    return str(launcher)


def make_clip(dst: Path, seconds: float, size: str = "320x180", rate: int = 15) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    ff.run(["-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate={rate}:duration={seconds}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(dst)], what="make_clip")
    return dst


# The shape `bench.meta_view` hands to `meta_narration` (and `bench-meta` derives from DIR/bench.json):
# `rows` as `bench_report.aggregate` writes them (`runs` is an int, minutes over the PASS runs), the
# scenario as {name, slug}.  The bench's own `drivers` key is the spec list and must never be read as rows.
BENCH = {
    "scenario": {"name": "Chat with Manuals", "slug": "chat-with-manuals"},
    "drivers": [{"kind": "template", "spec": "template", "model": None, "base_url": None, "slug": "template"}],
    "rows": [
        {"driver": "template", "kind": "template", "slug": "template", "model": None, "verdict": "PASS",
         "verdicts": ["PASS"], "runs": 1, "passed_runs": 1, "total_minutes": 4.0, "pass_minutes": 4.0,
         "narration_source": "template", "narration_sources": {"template": 1}, "tool_calls": None},
        {"driver": "opencode:lmstudio/qwen3-coder-30b", "kind": "opencode", "slug": "opencode-lmstudio-qwen3-coder-30b",
         "model": "lmstudio/qwen3-coder-30b", "verdict": "PASS", "verdicts": ["PASS", "PASS"], "runs": 2,
         "passed_runs": 2, "total_minutes": 14.3, "pass_minutes": 14.3, "all_runs_minutes": 14.3,
         "narration_source": "agent", "narration_sources": {"agent": 2}, "tool_calls": 12, "kit_tool_calls": 8},
        {"driver": "opencode:anthropic/claude-sonnet-4-5", "model": "anthropic/claude-sonnet-4-5",
         "verdict": "ERROR", "total_minutes": 0.5, "failing_stage": "record",
         "narration_source": "agent", "tool_calls": [{"command": "a"}, {"command": "b"}]},   # odd, tolerated
    ],
}
BASELINE = [{"driver": "manual", "model": "codex (cloud)", "date": "2026-08-31", "verdict": "PASS",
             "total_minutes": 95, "notes": "first run incl. installs", "narration_source": "cloud model",
             "tool_calls": None}]


# --------------------------------------------------------------------------- argv per OS


def test_build_argv_windows_grabs_the_desktop(tmp_path):
    argv = bm.build_argv("win32", tmp_path / "screen.mp4", 15)
    assert argv[argv.index("-f") + 1] == "gdigrab"
    assert argv[argv.index("-i") + 1] == "desktop"
    assert argv[argv.index("-framerate") + 1] == "15"
    assert "-offset_x" not in argv and "-video_size" not in argv
    assert argv[-1] == str(tmp_path / "screen.mp4")
    assert "libx264" in argv and "yuv420p" in argv
    assert argv[argv.index("-vf") + 1].startswith("scale=trunc(iw/2)*2")
    assert bm.build_argv("Windows", "x.mp4", 15) == bm.build_argv("win32", "x.mp4", 15)


def test_build_argv_macos_names_the_screen_device():
    argv = bm.build_argv("darwin", "out.mp4", 15, display_index=1)
    assert argv[argv.index("-f") + 1] == "avfoundation"
    assert argv[argv.index("-i") + 1] == "Capture screen 1:none"
    assert "-capture_cursor" in argv
    assert "crop=" not in " ".join(argv)
    assert bm.build_argv("Darwin", "out.mp4", 15)[argv.index("-i") + 1] == "Capture screen 0:none"


def test_build_argv_linux_uses_display_env(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":1")
    argv = bm.build_argv("linux", "out.mp4", 15)
    assert argv[argv.index("-f") + 1] == "x11grab"
    assert argv[argv.index("-i") + 1] == ":1.0"
    assert "-video_size" not in argv
    monkeypatch.setenv("DISPLAY", "localhost:2.7")
    assert bm.build_argv("Linux", "out.mp4", 15, display_index=1)[argv.index("-i") + 1] == "localhost:2.1"
    monkeypatch.delenv("DISPLAY")
    assert bm.build_argv("linux", "out.mp4", 15)[argv.index("-i") + 1] == ":0.0"
    assert bm.build_argv("linux", "out.mp4", 15, display="unix:3")[argv.index("-i") + 1] == "unix:3.0"


def test_build_argv_fps_and_unknown_os():
    argv = bm.build_argv("freebsd", "o.mp4", 0)
    assert argv[argv.index("-framerate") + 1] == str(bm.DISPLAY_FPS)   # 0 -> default 15
    assert argv[argv.index("-f") + 1] == "x11grab"


# --------------------------------------------------------------------------- start/stop plumbing


def test_display_capture_start_stop_with_fake_ffmpeg(tmp_path):
    exe = fake_ffmpeg(tmp_path)
    out = tmp_path / "meta" / "clips" / "00-template-r1.mp4"
    cap = bm.DisplayCapture(out, fps=15, display_index=0, ffmpeg=exe, os_name="linux", startup_wait=0.3)
    t0 = cap.start()
    assert cap.now() >= 0.0
    assert cap.start() == t0                        # idempotent
    path = cap.stop()
    assert path == out and out.is_file() and out.stat().st_size > 0
    assert cap.t_stop is not None and cap.t_stop >= 0.3
    side = json.loads(Path(str(out) + ".argv.json").read_text(encoding="utf-8"))
    assert side["stopped_by"] == "q"
    assert side["argv"] == bm.build_argv("linux", out, 15, 0)
    assert cap.stop() == out                        # second stop is a no-op
    assert cap.log_path.is_file()
    assert "fake ffmpeg" in cap.log_path.read_text(encoding="utf-8", errors="replace")


def test_display_capture_reports_a_grabber_that_dies(tmp_path, monkeypatch):
    monkeypatch.setenv("FFMPEG_FAKE_DIE", "1")
    cap = bm.DisplayCapture(tmp_path / "x.mp4", ffmpeg=fake_ffmpeg(tmp_path), os_name="win32", startup_wait=0.5)
    with pytest.raises(CaptureError, match="exited immediately"):
        cap.start()
    assert "FFMPEG_FAKE_DIE" in cap._log_tail()
    assert cap._proc.stdin.closed and cap._log.closed          # no pipe fd leaks for a grabber that died
    cap.abort()
    assert cap._proc.stdin.closed


def test_display_capture_terminates_a_grabber_that_ignores_q(tmp_path, monkeypatch):
    monkeypatch.setenv("FFMPEG_FAKE_IGNORE_Q", "1")
    # short safety timer: on Windows terminate() ends the .cmd launcher, not the python fake, which then
    # lives on until this timer fires - keep that orphan (and the file it writes into tmp_path) short-lived
    monkeypatch.setenv("FFMPEG_FAKE_MAX_SECONDS", "3")
    cap = bm.DisplayCapture(tmp_path / "x.mp4", ffmpeg=fake_ffmpeg(tmp_path), os_name="darwin",
                            startup_wait=0.3, stop_timeout=1.0)
    cap.start()
    with pytest.raises(CaptureError, match="display grabber failed"):
        cap.stop()                                   # terminated: nothing written -> error


def test_display_capture_abort_never_raises(tmp_path):
    cap = bm.DisplayCapture(tmp_path / "x.mp4", ffmpeg=fake_ffmpeg(tmp_path), os_name="linux", startup_wait=0.2)
    cap.abort()                                      # never started
    cap2 = bm.DisplayCapture(tmp_path / "y.mp4", ffmpeg=fake_ffmpeg(tmp_path), os_name="linux", startup_wait=0.2)
    cap2.start()
    cap2.abort()
    assert cap2.t_stop is not None
    with pytest.raises(CaptureError, match="never started"):
        bm.DisplayCapture(tmp_path / "z.mp4", ffmpeg=fake_ffmpeg(tmp_path)).stop()


def test_display_capture_missing_binary(tmp_path):
    cap = bm.DisplayCapture(tmp_path / "x.mp4", ffmpeg=str(tmp_path / "no-such-ffmpeg"), os_name="linux")
    with pytest.raises(CaptureError, match="could not start"):
        cap.start()


def test_meta_clip_path_layout(tmp_path):
    p = bm.meta_clip_path(tmp_path, 1, "opencode-lmstudio-qwen3", repeat=2)
    assert p == tmp_path / "meta" / "clips" / "01-opencode-lmstudio-qwen3-r2.mp4"


# --------------------------------------------------------------------------- spoken numbers + narration


def test_spoken_numbers():
    assert bm.spoken_number(4) == "four"
    assert bm.spoken_number(12) == "12"
    assert bm.spoken_number(3.6) == "four"
    assert bm.spoken_minutes(4.0) == "four minutes"
    assert bm.spoken_minutes(4.3) == "about four minutes"
    assert bm.spoken_minutes(1.0) == "one minute"
    assert bm.spoken_minutes(1.2) == "about a minute"
    assert bm.spoken_minutes(0.5) == "30 seconds"
    assert bm.spoken_minutes(0.02) == "a few seconds"
    assert bm.spoken_minutes(95) == "95 minutes"
    assert bm.spoken_tool_calls(None) == "no tool calls"
    assert bm.spoken_tool_calls(0) == "no tool calls"
    assert bm.spoken_tool_calls(1) == "one tool call"
    assert bm.spoken_tool_calls(12) == "12 tool calls"
    assert bm.spoken_tool_calls(["a", "b", "c"]) == "three tool calls"
    assert bm.spoken_tool_calls({"count": 7}) == "seven tool calls"
    # a fractional mean over repeats is spoken as "about" (the table shows the exact mean)
    assert bm.spoken_tool_calls(12.5) == "about 12 tool calls"
    assert bm.spoken_tool_calls(8.333) == "about eight tool calls"
    assert bm.spoken_tool_calls(1.4) == "about one tool call"
    assert bm.spoken_tool_calls(8.0) == "eight tool calls"
    assert bm.spoken_count(5.333) == "about five" and bm.spoken_count(8) == "eight"
    assert bm.spoken_list(["a", "b", "c"]) == "a, b and c"


def test_driver_slug_and_spoken_driver():
    assert bm.driver_slug("opencode:lmstudio/qwen3-coder@http://127.0.0.1:1/v1") == "opencode-lmstudio-qwen3-coder"
    assert bm.driver_slug("template") == "template"
    assert bm.driver_slug("manual", "codex (cloud)") == "manual-codex-cloud"
    assert bm.spoken_driver("template") == "the template driver, with no model at all"
    assert bm.spoken_driver("opencode:lmstudio/qwen3-coder-30b") == "OpenCode with lmstudio qwen3-coder-30b"
    assert bm.spoken_driver("llm:http://x|qwen3") == "the LLM narration driver with qwen3"
    assert bm.spoken_driver("manual", "codex (cloud)") == "a manual run with codex (cloud)"


def test_meta_narration_speaks_the_numbers():
    narr = meta = bm.meta_narration(BENCH, BASELINE)
    assert set(narr) == {"intro", "outro", "steps"}
    assert narr["intro"].startswith("This is the smoke kit running itself under three drivers:")
    assert "Chat with Manuals" in narr["intro"]
    ids = [s["id"] for s in narr["steps"]]
    assert ids == ["template", "opencode-lmstudio-qwen3-coder-30b", "opencode-anthropic-claude-sonnet-4-5"]
    t, q, c = (s["text"] for s in meta["steps"])
    assert "four minutes" in t and "no tool calls" in t and "passed" in t
    assert "the scenario template" in t
    assert q.startswith("Averaged over two runs under OpenCode with lmstudio qwen3-coder-30b, a run took about 14 minutes")
    assert "12 tool calls including file reads, eight of them kit commands" in q and "passed both runs" in q
    assert "the agent itself" in q
    assert "30 seconds" in c and "two tool calls" in c and "hit an error in the record stage" in c
    assert narr["outro"].startswith("For comparison, a manual run with codex (cloud) on 2026-08-31 took 95 minutes")
    # the template runs no model: never "the fastest driver", but quoted as the pipeline-only baseline
    assert ("The fastest passing model driver here, OpenCode with lmstudio qwen3-coder-30b, took about 14 minutes. "
            "The template driver, the pipeline on its own with no model, took four minutes.") in narr["outro"]
    assert "fastest passing model driver here, the template" not in narr["outro"]
    assert "the local model" not in " ".join(s["text"] for s in narr["steps"])
    for seg in [narr["intro"], narr["outro"], *(s["text"] for s in narr["steps"])]:
        assert "\n" not in seg and seg.endswith(".")
        assert not any(ch in seg for ch in "/_")      # spoken text, no path-ish tokens


def test_meta_narration_without_baseline_and_with_manual_rows_merged():
    narr = bm.meta_narration(BENCH, None)
    assert narr["outro"].startswith("No manual baseline was given. The fastest passing model driver was OpenCode with "
                                    "lmstudio qwen3-coder-30b at about 14 minutes. The template driver, the pipeline "
                                    "on its own with no model, took four minutes.")
    # every baseline entry is spoken, not only the first three
    many = [{**BASELINE[0], "model": f"run {i}", "date": None, "total_minutes": 20 + i} for i in range(5)]
    outro = bm.meta_narration(BENCH, many)["outro"]
    assert all(f"a manual run with run {i} took {20 + i} minutes" in outro for i in range(5))
    # the llm driver talks to any OpenAI-compatible endpoint, hosted or local: never called "the local model"
    hosted = {"driver": "llm:https://api.openai.com/v1|gpt-4o", "kind": "llm", "model": "gpt-4o", "verdict": "PASS",
              "total_minutes": 3.0, "narration_source": "llm", "narration_sources": {"llm": 1}}
    text = bm.meta_narration({"rows": [hosted]})["steps"][0]["text"]
    assert "The narration came from the LLM endpoint." in text and "local" not in text
    merged = {"scenario": "x", "rows": [*BENCH["rows"][:1], {**BASELINE[0], "manual": True}]}
    narr2 = bm.meta_narration(merged)
    assert [s["id"] for s in narr2["steps"]] == ["template"]        # manual rows are baseline, not segments
    assert "For comparison, a manual run" in narr2["outro"]
    assert narr2["intro"] == "This is the smoke kit running itself under the template driver, with no model at all. The scenario is x, the same one every time."


def test_meta_narration_tolerates_odd_shapes():
    narr = bm.meta_narration([], [])
    assert narr["steps"] == [] and "nothing to compare" in narr["intro"] and "No driver passed" in narr["outro"]
    rows = [{"driver": "opencode:a/b", "verdict": "FAIL", "failing_step": "ask"},
            {"driver": "opencode:a/b", "verdict": "PASS", "wall_s": 61, "tool_calls": 9, "fallback": True}]
    narr = bm.meta_narration({"drivers": rows})            # a `drivers` list of result rows still counts
    assert [s["id"] for s in narr["steps"]] == ["opencode-a-b", "opencode-a-b-2"]
    assert "failed at the ask step" in narr["steps"][0]["text"]
    assert "the run time was not recorded" in narr["steps"][0]["text"]
    assert "fell back to the template narration" in narr["steps"][1]["text"]
    assert "one minute" in narr["steps"][1]["text"]
    # the bench's `drivers` key is the Driver spec list (kind/spec/slug, no `driver`): never rows
    specs = [{"kind": "opencode", "spec": "opencode:a/b", "model": "a/b", "base_url": None, "slug": "opencode-a-b"}]
    assert bm.bench_rows({"drivers": specs}) == []
    assert bm.bench_rows({"drivers": specs, "rows": rows}) == rows
    assert bm.bench_rows({"drivers": specs, "rows": []}) == []
    assert bm.bench_rows(BENCH) == BENCH["rows"]
    assert [s["id"] for s in bm.meta_narration({"drivers": specs, "scenario": "/abs/x.json"})["steps"]] == []


def test_meta_narration_speaks_repeats_as_averages_with_per_run_verdicts():
    row = {"driver": "opencode:lmstudio/qwen3", "kind": "opencode", "slug": "opencode-lmstudio-qwen3",
           "model": "lmstudio/qwen3", "verdict": "PASS 2/3", "verdicts": ["PASS", "ERROR", "PASS"], "runs": 3,
           "passed_runs": 2, "total_minutes": 5.2, "pass_minutes": 5.2, "all_runs_minutes": 23.4,
           "run_minutes": [5.0, 60.0, 5.3], "narration_source": "agent/template",
           "narration_sources": {"agent": 2, "template": 1}, "tool_calls": 8.333, "kit_tool_calls": 5.333}
    text = bm.meta_narration({"scenario": {"name": "S", "slug": "s"}, "rows": [row]})["steps"][0]["text"]
    assert text.startswith("Averaged over three runs under OpenCode with lmstudio qwen3, a passing run took about five minutes")
    assert "23 minutes" not in text                       # the timed-out repeat is not folded into the spoken mean
    assert "used about eight tool calls including file reads, about five of them kit commands" in text
    assert "passed two of three runs" in text and "pass 2/3" not in text.lower()
    assert "The narration came from the agent itself on two runs and the scenario template on one run." in text
    narr = bm.meta_narration({"scenario": {"name": "S", "slug": "s"}, "rows": [row]})
    assert "The fastest passing model driver was OpenCode with lmstudio qwen3 at about five minutes" in narr["outro"]
    # the label alone (older rows without `verdicts`) is understood too, and mixed failures are spelled out
    assert bm._verdict_phrase({"verdict": "PASS 2/3"}) == "passed two of three runs"
    assert bm._verdict_phrase({"verdict": "PASS 0/2 (FAIL 1, ERROR 1)", "verdicts": ["FAIL", "ERROR"]}) == \
        "passed none of the two runs, failing on one and erroring on one"
    assert bm._verdict_phrase({"verdicts": ["ERROR", "ERROR"]}) == "hit an error on both runs"
    assert bm._verdict_phrase({"verdicts": ["PASS", "PASS", "PASS"]}) == "passed all three runs"
    assert bm._passing_minutes({"verdict": "PASS 0/2", "total_minutes": 3.0}) is None
    assert bm._passing_minutes({"verdict": "PASS 1/2", "total_minutes": 3.0}) == 3.0


# --------------------------------------------------------------------------- pure plan


def test_plan_meta_sequential_with_gaps_and_padding():
    segs = [("intro", 2.0), ("a", 3.0), ("b", 1.0), ("outro", 2.0)]
    plan = bm.plan_meta(segs, video_seconds=20.0, gap=0.5, tail=0.5)
    assert [(p["id"], p["t"]) for p in plan["audio"]] == [("intro", 0.0), ("a", 2.5), ("b", 6.0), ("outro", 7.5)]
    assert plan["audio_end"] == 9.5 and plan["total"] == 20.0 and plan["pad_seconds"] == 0.0
    plan = bm.plan_meta(segs, video_seconds=4.0)
    assert plan["total"] == 10.0 and plan["pad_seconds"] == 6.0          # narration longer: pad the picture
    aligned = bm.plan_meta(segs, video_seconds=20.0, clip_starts=[0.0, 12.0])
    assert [p["t"] for p in aligned["audio"]] == [0.0, 2.5, 12.0, 13.5]
    assert bm.plan_meta([], 3.0)["total"] == 3.0
    # a driver without a clip of its own (None) keeps the sequential placement
    assert [p["t"] for p in bm.plan_meta(segs, 20.0, clip_starts=[None, 12.0])["audio"]] == [0.0, 2.5, 12.0, 13.5]


def test_clip_starts_match_driver_segments_by_slug(tmp_path):
    """`bench --record-screen` writes one clip per run, the narration has one segment per driver: clips are
    matched by the slug in their name, so a template driver plus an opencode driver at --repeat 2 does not pin
    the template narration to the opencode r1 clip."""
    assert bm.clip_slug("01-opencode-lmstudio-qwen3-r2.mp4") == "opencode-lmstudio-qwen3"
    assert bm.clip_slug(str(tmp_path / "00-template-r1.mp4")) == "template"
    assert bm.clip_slug("screen.mp4") is None
    clips = [Path("01-template-r1.mp4"), Path("02-opencode-x-y-r1.mp4"), Path("03-opencode-x-y-r2.mp4")]
    assert bm.clip_starts_by_slug(clips, [10.0, 20.0, 30.0], ["template", "opencode-x-y", "llm-z"]) == [0.0, 10.0, None]
    assert bm.clip_starts_by_slug([Path("a.mp4")], [5.0], ["template"]) == [None]


def test_narration_segments_order_and_skips_empty():
    narr = {"intro": " Hi. ", "steps": [{"id": "a", "text": "A."}, {"id": "b", "text": ""}, "junk"], "outro": ""}
    assert bm.narration_segments(narr) == [("intro", "Hi."), ("a", "A.")]
    assert bm.video_chain_meta(0.0) == [f"[0:v]fps={bm.FPS},format=yuv420p[vout]"]
    assert "tpad=stop_mode=clone:stop_duration=2.500" in bm.video_chain_meta(2.5)[0]


# --------------------------------------------------------------------------- concat + meta video (real ffmpeg)


@pytest.fixture(scope="module")
def clips(tmp_path_factory) -> list[Path]:
    d = tmp_path_factory.mktemp("clips")
    return [make_clip(d / "00-template-r1.mp4", 2.0), make_clip(d / "01-opencode-r1.mp4", 2.0, size="426x240")]


def test_concat_videos_joins_clips_of_different_sizes(clips, tmp_path):
    out = ffmpeg_concat.concat_videos(clips, tmp_path / "joined.mp4")
    info = ff.media_info(out)
    assert (info["width"], info["height"]) == (320, 180)        # first clip's size
    assert abs(info["duration"] - 4.0) <= 0.2
    assert not info["has_audio"]
    with pytest.raises(ff.FfmpegError, match="not found"):
        ffmpeg_concat.concat_videos([tmp_path / "missing.mp4"], tmp_path / "x.mp4")
    with pytest.raises(ff.FfmpegError, match="no clips"):
        ffmpeg_concat.concat_videos([], tmp_path / "x.mp4")
    assert ffmpeg_concat.concat_filter(2, 321, 181, 30).endswith("concat=n=2:v=1:a=0[vout]")
    assert "scale=320:180" in ffmpeg_concat.concat_filter(2, 321, 181, 30)


@pytest.fixture(scope="module")
def meta_video(clips, tmp_path_factory) -> dict:
    bench_dir = tmp_path_factory.mktemp("bench")
    narr = bm.meta_narration(BENCH, BASELINE)
    out = bench_dir / "meta" / "chat-with-manuals-bench.mp4"
    final = bm.build_meta_video(clips, narr, out, tts="tone", ref=None)
    return {"bench_dir": bench_dir, "final": final, "narr": narr}


def test_meta_video_from_two_clips_has_audio_and_expected_duration(meta_video):
    final, bench_dir = meta_video["final"], meta_video["bench_dir"]
    assert final.is_file() and final.stat().st_size > 0
    log = json.loads((bench_dir / "meta" / "meta-edit.json").read_text(encoding="utf-8"))
    assert log["ok"] and log["error"] is None
    plan = log["plan"]
    durs = log["durations"]
    ids = ["intro", *(s["id"] for s in meta_video["narr"]["steps"]), "outro"]
    assert list(durs) == ids and [a["id"] for a in plan["audio"]] == ids
    expected_end = sum(durs.values()) + 0.5 * (len(ids) - 1)
    assert plan["audio_end"] == pytest.approx(expected_end, abs=1e-3)
    assert plan["video_seconds"] == pytest.approx(4.0, abs=0.2)
    assert plan["pad_seconds"] > 0 and plan["total"] == pytest.approx(expected_end + 0.5, abs=1e-3)
    info = ff.media_info(final)
    assert info["has_audio"]
    assert abs(info["duration"] - plan["total"]) <= 0.3
    assert abs((ff.stream_duration(final, "video") or 0) - plan["total"]) <= 0.3
    vol = ff.volumedetect(final)
    assert vol["mean_volume"] is not None and vol["mean_volume"] > -35
    assert (bench_dir / "meta" / "narration.json").is_file()
    assert (bench_dir / "meta" / "audio" / "seg-intro.wav").is_file()
    assert (bench_dir / "meta" / "concat.mp4").is_file()
    assert "loudnorm" in log["filter_complex"] and "tpad=stop_mode=clone" in log["filter_complex"]


def test_build_meta_video_input_errors(tmp_path, clips):
    with pytest.raises(bm.MetaError, match="no screen recordings"):
        bm.build_meta_video([], {"intro": "x"}, tmp_path / "m.mp4", tts="tone")
    with pytest.raises(bm.MetaError, match="not found"):
        bm.build_meta_video([tmp_path / "nope.mp4"], {"intro": "x"}, tmp_path / "m.mp4", tts="tone")
    with pytest.raises(bm.MetaError, match="no segments"):
        bm.build_meta_video(clips, {"intro": "", "steps": [], "outro": ""}, tmp_path / "m.mp4", tts="tone")


# --------------------------------------------------------------------------- bench-meta command


def _parser():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd")
    run_map: dict = {}
    bm.register(sub, run_map)
    assert run_map["bench-meta"] is bm.cmd_bench_meta
    return p


def test_bench_meta_command_end_to_end(clips, tmp_path, capsys):
    bench_dir = tmp_path / "bench"
    clip_dir = bench_dir / "meta" / "clips"
    clip_dir.mkdir(parents=True)
    for c in clips:
        shutil.copy(c, clip_dir / c.name)
    (bench_dir / "bench.json").write_text(json.dumps(BENCH), encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(BASELINE), encoding="utf-8")
    args = _parser().parse_args(["bench-meta", "--out", str(bench_dir), "--baseline", str(baseline),
                                 "--tts", "tone", "--gap", "0.25", "--align-clips"])
    assert args.fn(args) == 0
    line = capsys.readouterr().out.strip()
    assert line.startswith("bench-meta: ") and "2 clip(s), 5 segments" in line
    final = bench_dir / "meta" / "chat-with-manuals-bench.mp4"
    assert final.is_file()
    log = json.loads((bench_dir / "logs" / "bench-meta.json").read_text(encoding="utf-8"))
    assert log["exit_code"] == 0 and log["final"] == str(final) and log["tts"] == "tone"
    edit_log = json.loads((bench_dir / "meta" / "meta-edit.json").read_text(encoding="utf-8"))
    assert edit_log["plan"]["gap"] == 0.25
    # --align-clips: the template clip (00-template-r1.mp4) matched by slug, the two opencode drivers have none
    assert edit_log["clip_starts"] == [0.0, None, None] and edit_log["align_note"] is None
    assert edit_log["timeout_s"] == 900 and "-preset" in edit_log["argv"]
    assert edit_log["argv"][edit_log["argv"].index("-preset") + 1] == "veryfast"
    narr = json.loads((bench_dir / "meta" / "narration.json").read_text(encoding="utf-8"))
    assert "For comparison" in narr["outro"]


def _real_bench_dir(bench_dir: Path, baseline: list[dict]) -> dict:
    """Write DIR/bench.json exactly the way `bench` does (via `bench_report.write`): `drivers` = the
    spec list, `scenario` = a path string, `rows` aggregated from per-run records, `baseline` stored."""
    def run(slug, spec, kind, model, n, verdict, wall, source, tool_calls=None):
        rec = {"driver": spec, "kind": kind, "model": model, "base_url": None, "slug": slug, "run": n,
               "out": str(bench_dir / "runs" / slug / f"r{n}"), "started": "t0", "finished": "t1", "wall_s": wall,
               "stages": {s: None for s in bench_report.STAGES}, "verdict": verdict, "exit_code": 0,
               "failing_stage": None if verdict == "PASS" else "record", "failing_step": None, "error": None,
               "narration": {"source": source, "validation_errors": 0, "retries": 0, "total_words": 40,
                             "references_on_screen": 0.6, "estimated_seconds": 15.0, "words_per_segment": {}},
               "audio": {"total_seconds": 15.0, "segments": {}},
               "video": {"duration": 20.0 if verdict == "PASS" else None, "verify_pass": verdict == "PASS",
                         "checks": [], "path": None},
               "opencode": None, "llm": None, "env": None, "report": None}
        if kind == "opencode":
            rec["opencode"] = {"tool_calls": tool_calls, "kit_tool_calls": 8, "commands": [], "kit_commands": [],
                               "assistant_messages": 1, "permission_prompts": 0, "denied": 0, "steps": 9,
                               "step_limit_reached": False, "tokens_in": 1, "tokens_out": 1, "tokens_total": 2,
                               "cost": 0.0, "narration_written_by_agent": source == "agent",
                               "used_narrate_template": source == "template"}
        return rec
    runs = [run("template", "template", "template", None, 1, "PASS", 240.0, "template"),
            run("opencode-fake-scripted", "opencode:fake/scripted@http://127.0.0.1:5/v1", "opencode", "fake/scripted",
                1, "PASS", 300.0, "agent", 12),
            run("opencode-fake-scripted", "opencode:fake/scripted@http://127.0.0.1:5/v1", "opencode", "fake/scripted",
                2, "ERROR", 3600.0, "template", 3)]
    bench = {"version": "x", "scenario": str(bench_dir.parent / "scenarios" / "fixture-pass.json"),
             "name": "Chat with Manuals (fixture, pass)", "slug": "fixture-pass", "started": "t0", "finished": "t1",
             "wall_s": 4140.0, "out": str(bench_dir), "repeat": 2,
             "drivers": [{"kind": "template", "spec": "template", "model": None, "base_url": None, "slug": "template"},
                         {"kind": "opencode", "spec": "opencode:fake/scripted@http://127.0.0.1:5/v1",
                          "model": "fake/scripted", "base_url": "http://127.0.0.1:5/v1", "slug": "opencode-fake-scripted"}],
             "args": {"tts": "tone", "headless": True}, "baseline_file": None, "baseline": baseline, "runs": runs,
             "screen_recordings": [], "interrupted": False}
    bench_report.write(bench_dir, bench)
    return json.loads((bench_dir / "bench.json").read_text(encoding="utf-8"))


def test_bench_meta_command_reads_a_real_bench_json(clips, tmp_path, capsys):
    """DIR/bench.json as `bench` writes it: `drivers` is the spec list and `scenario` a path, so the
    command must read `rows` (+ the stored baseline) and name the video after the scenario slug."""
    bench_dir = tmp_path / "bench"
    clip_dir = bench_dir / "meta" / "clips"
    clip_dir.mkdir(parents=True)
    for c in clips:
        shutil.copy(c, clip_dir / c.name)
    data = _real_bench_dir(bench_dir, bench_report.load_baseline(KIT / "bench" / "baseline.example.json"))
    assert bench_report.validate(data) == [] and "driver" not in data["drivers"][0]
    args = _parser().parse_args(["bench-meta", "--out", str(bench_dir), "--tts", "tone"])
    assert args.fn(args) == 0, capsys.readouterr()
    assert (bench_dir / "meta" / "fixture-pass-bench.mp4").is_file()
    narr = json.loads((bench_dir / "meta" / "narration.json").read_text(encoding="utf-8"))
    assert narr["intro"].startswith("This is the smoke kit running itself under two drivers: the template driver, "
                                    "with no model at all and OpenCode with fake scripted. The scenario is "
                                    "Chat with Manuals (fixture, pass)")
    assert "/" not in narr["intro"] and "unknown driver" not in narr["intro"]
    assert [s["id"] for s in narr["steps"]] == ["template", "opencode-fake-scripted"]
    t, o = (s["text"] for s in narr["steps"])
    assert t.startswith("Under the template driver, with no model at all, the run took four minutes")
    assert o.startswith("Averaged over two runs under OpenCode with fake scripted, a passing run took five minutes")
    assert "passed one of two runs" in o and "the agent itself on one run and the scenario template on one run" in o
    assert narr["outro"].startswith("For comparison, a manual run with codex (cloud)")     # baseline from bench.json
    assert ("The fastest passing model driver here, OpenCode with fake scripted, took five minutes. The template "
            "driver, the pipeline on its own with no model, took four minutes.") in narr["outro"]
    # --baseline in the {"entries": [...]} shape `bench --baseline` accepts; a bad shape is exit 4
    wrapped = tmp_path / "baseline.json"
    wrapped.write_text(json.dumps({"entries": [{"driver": "manual", "model": "claude (cloud)", "verdict": "PASS",
                                                "total_minutes": 12}]}), encoding="utf-8")
    args = _parser().parse_args(["bench-meta", "--out", str(bench_dir), "--tts", "tone", "--baseline", str(wrapped)])
    assert args.fn(args) == 0, capsys.readouterr()
    narr = json.loads((bench_dir / "meta" / "narration.json").read_text(encoding="utf-8"))
    assert narr["outro"].startswith("For comparison, a manual run with claude (cloud) took 12 minutes and passed.")
    assert "codex" not in narr["outro"]
    wrapped.write_text('{"x": 1}', encoding="utf-8")
    args = _parser().parse_args(["bench-meta", "--out", str(bench_dir), "--tts", "tone", "--baseline", str(wrapped)])
    assert args.fn(args) == 4
    assert "expected a JSON list of entries" in capsys.readouterr().err


def test_bench_meta_command_bad_input(tmp_path, capsys):
    bench_dir = tmp_path / "bench"
    args = _parser().parse_args(["bench-meta", "--out", str(bench_dir)])
    assert args.fn(args) == 4
    assert "bench.json" in capsys.readouterr().err
    log = json.loads((bench_dir / "logs" / "bench-meta.json").read_text(encoding="utf-8"))
    assert log["exit_code"] == 4 and "error" in log

    (bench_dir / "bench.json").write_text(json.dumps(BENCH), encoding="utf-8")
    args = _parser().parse_args(["bench-meta", "--out", str(bench_dir), "--tts", "tone"])
    assert args.fn(args) == 4                        # no clips anywhere
    assert "no screen recordings" in capsys.readouterr().err

    args = _parser().parse_args(["bench-meta", "--out", str(bench_dir), "--clips", str(tmp_path / "gone.mp4")])
    assert args.fn(args) == 4
    assert "not found" in capsys.readouterr().err

    (bench_dir / "bench.json").write_text("{not json", encoding="utf-8")
    args = _parser().parse_args(["bench-meta", "--out", str(bench_dir)])
    assert args.fn(args) == 4
    assert "not valid JSON" in capsys.readouterr().err


def test_bench_meta_command_pipeline_error_is_exit_3(tmp_path, capsys, monkeypatch):
    bench_dir = tmp_path / "bench"
    bench_dir.mkdir()
    (bench_dir / "bench.json").write_text(json.dumps(BENCH), encoding="utf-8")
    bad_clip = tmp_path / "bad.mp4"
    bad_clip.write_bytes(b"not a video")
    args = _parser().parse_args(["bench-meta", "--out", str(bench_dir), "--clips", str(bad_clip), "--tts", "tone"])
    assert args.fn(args) == 3
    assert capsys.readouterr().err.startswith("error: bench-meta:")
    log = json.loads((bench_dir / "logs" / "bench-meta.json").read_text(encoding="utf-8"))
    assert log["exit_code"] == 3
