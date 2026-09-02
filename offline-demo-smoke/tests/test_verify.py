"""``verify.check`` on a built synthetic video (passes) and on a black/silent
one (specific checks fail)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from demo_smoke import edit, verify
from demo_smoke import ffmpeg_util as ff
from tests.test_edit_build import SCENARIO, make_project


def _by_name(res: dict) -> dict:
    return {c["name"]: c for c in res["checks"]}


@pytest.fixture(scope="module")
def good(tmp_path_factory) -> dict:
    out = make_project(tmp_path_factory.mktemp("verify") / "out")
    final = edit.build(out, SCENARIO)
    return {"out": out, "final": final, "result": verify.check(out, SCENARIO)}


def test_built_video_passes_every_check(good):
    res = good["result"]
    names = list(_by_name(res))
    assert names == ["duration", "audio_present", "av_length_match", "no_black_start",
                     "no_black_end", "narration_audible", "thumbnails"]
    failed = [c for c in res["checks"] if not c["pass"]]
    assert res["pass"] is True, failed
    assert res["duration"] == pytest.approx(7.45, abs=0.3)
    for c in res["checks"]:
        assert c["detail"] and "\n" not in c["detail"]
    assert "mean_volume" in _by_name(res)["narration_audible"]["detail"]
    assert "diff" in _by_name(res)["av_length_match"]["detail"]


def test_thumbnails_and_log_are_written(good):
    out, res = good["out"], good["result"]
    assert [Path(p).name for p in res["thumbnails"]] == ["thumb-10.png", "thumb-50.png", "thumb-90.png"]
    for p in res["thumbnails"]:
        assert Path(p).is_file() and Path(p).stat().st_size > 0
        assert Path(p).parent == out / "final"
        assert Path(p).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    log = json.loads((out / "logs" / "verify.json").read_text(encoding="utf-8"))
    assert log["pass"] is True
    assert log["checks"] == res["checks"]
    assert log["final"] == str(good["final"])


def test_black_silent_video_fails_the_specific_checks(tmp_path):
    out = tmp_path / "out"
    final = out / "final" / "synthetic-demo.mp4"
    final.parent.mkdir(parents=True)
    ff.run(["-y", "-f", "lavfi", "-i", "color=black:size=640x360:rate=30:duration=4",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "4",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(final)], what="make black video")
    res = verify.check(out, SCENARIO)
    by = _by_name(res)
    assert res["pass"] is False
    assert not by["no_black_start"]["pass"] and "black at" in by["no_black_start"]["detail"]
    assert not by["no_black_end"]["pass"] and "black at" in by["no_black_end"]["detail"]
    assert not by["narration_audible"]["pass"]
    assert "mean_volume" in by["narration_audible"]["detail"]
    assert by["duration"]["pass"] and by["audio_present"]["pass"]
    assert by["av_length_match"]["pass"] and by["thumbnails"]["pass"]
    assert (out / "logs" / "verify.json").is_file()


def test_video_without_audio_fails_audio_checks(tmp_path):
    out = tmp_path / "out"
    final = out / "final" / "synthetic-demo.mp4"
    final.parent.mkdir(parents=True)
    ff.run(["-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30:duration=2",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(final)],
           what="make mute video")
    by = _by_name(verify.check(out, SCENARIO))
    assert not by["audio_present"]["pass"]
    assert not by["av_length_match"]["pass"]
    assert not by["narration_audible"]["pass"]
    assert by["no_black_start"]["pass"] and by["no_black_end"]["pass"]


def test_too_long_video_fails_duration(tmp_path):
    out = tmp_path / "out"
    final = out / "final" / "synthetic-demo.mp4"
    final.parent.mkdir(parents=True)
    ff.run(["-y", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=440", "-t", "3",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(final)], what="make short video")
    by = _by_name(verify.check(out, {**SCENARIO, "max_length_seconds": -9}))   # limit 1 s
    assert not by["duration"]["pass"]
    assert "limit 1 s" in by["duration"]["detail"]


def test_missing_final_is_a_one_line_error(tmp_path):
    with pytest.raises(verify.VerifyError, match="run `edit` first") as ei:
        verify.check(tmp_path / "out", SCENARIO)
    assert "\n" not in str(ei.value)


def test_find_final_falls_back_to_any_mp4(tmp_path):
    final_dir = tmp_path / "out" / "final"
    final_dir.mkdir(parents=True)
    other = final_dir / "renamed.mp4"
    other.write_bytes(b"x")
    assert verify.find_final(tmp_path / "out", SCENARIO) == other
