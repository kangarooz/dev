"""``edit.build`` on synthetic media (lavfi testsrc + sine).  Also hosts the
synthetic-project helpers reused by ``test_verify.py``."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from demo_smoke import edit
from demo_smoke import ffmpeg_util as ff

SCENARIO = {"name": "Synthetic Demo", "slug": "synthetic-demo", "max_length_seconds": 30,
            "viewport": {"width": 640, "height": 360}}

# intro 0-1.5 ; open narr 1.5-2.5, wait window 2.6-6.0 (3.4 s, sped 4x) ; ask 6.5-8.0 ; outro 8.0-9.0
MARKERS = {
    "capture_start_epoch": 1700000000.0, "intro_t": 0.0, "outro_t": 8.0, "end_t": 9.0,
    "steps": [
        {"id": "open", "t_start": 1.5, "t_end": 6.2, "status": "PASS", "wait_windows": [[2.6, 6.0]]},
        {"id": "ask", "t_start": 6.5, "t_end": 8.0, "status": "PASS", "wait_windows": []},
    ],
}
DURATIONS = {"intro": 1.5, "open": 1.0, "ask": 1.5, "outro": 1.0}


def make_video(dst: Path, seconds: float, size: str = "640x360", source: str = "testsrc") -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    src = f"{source}=size={size}:rate=30:duration={seconds}"
    ff.run(["-y", "-f", "lavfi", "-i", src, "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", str(dst)], what="make_video")
    return dst


def make_wav(dst: Path, seconds: float, freq: int = 440) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    ff.run(["-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
            "-ac", "1", "-ar", "24000", str(dst)], what="make_wav")
    return dst


def make_project(out: Path, capture_seconds: float = 12.0, size: str = "640x360",
                 markers: dict | None = None, durations: dict | None = None) -> Path:
    """raw/capture.mp4 + audio/seg-*.wav + audio/durations.json + logs/markers.json."""
    markers = markers or MARKERS
    durations = durations or DURATIONS
    make_video(out / "raw" / "capture.mp4", capture_seconds, size=size)
    for i, (sid, secs) in enumerate(durations.items()):
        make_wav(out / "audio" / f"seg-{sid}.wav", secs, freq=330 + 110 * i)
    (out / "audio" / "durations.json").write_text(json.dumps(durations), encoding="utf-8")
    (out / "logs").mkdir(parents=True, exist_ok=True)
    (out / "logs" / "markers.json").write_text(json.dumps(markers, indent=2), encoding="utf-8")
    return out


def silence_ends(path: Path, noise_db: int = -40, min_d: float = 0.3) -> list[float]:
    cp = ff.run(["-i", str(path), "-vn", "-af", f"silencedetect=n={noise_db}dB:d={min_d}",
                 "-f", "null", "-"], what="silencedetect")
    return [float(x) for x in re.findall(r"silence_end:\s*(\d+(?:\.\d+)?)", cp.stderr)]


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> dict:
    out = make_project(tmp_path_factory.mktemp("edit") / "out")
    final = edit.build(out, SCENARIO)
    return {"out": out, "final": final}


def test_build_writes_final_mp4_matching_the_plan(built):
    out, final = built["out"], built["final"]
    assert final == out / "final" / "synthetic-demo.mp4"
    assert final.is_file() and final.stat().st_size > 0
    log = json.loads((out / "logs" / "edit.json").read_text(encoding="utf-8"))
    plan = log["plan"]
    saved = 3.4 - 3.4 / 4.0
    assert plan["total"] == pytest.approx(10.0 - saved, abs=1e-3)      # end_t 9.0 + 1.0 tail
    info = ff.media_info(final)
    assert abs(info["duration"] - plan["total"]) <= 0.3
    assert info["has_audio"] and info["sample_rate"] == 48000
    assert (info["width"], info["height"]) == (640, 360)
    assert log["ok"] is True and log["crop"] is None


def test_edit_json_records_the_exact_command(built):
    out = built["out"]
    log = json.loads((out / "logs" / "edit.json").read_text(encoding="utf-8"))
    argv = log["argv"]
    assert argv[0] == ff.find_ffmpeg()
    assert "-filter_complex_script" in argv and "libx264" in argv and "aac" in argv
    assert argv[argv.index("-crf") + 1] == "20" and argv[argv.index("-b:a") + 1] == "160k"
    script = Path(argv[argv.index("-filter_complex_script") + 1])
    assert script.is_file()
    graph = script.read_text(encoding="utf-8")
    assert graph == log["filter_complex"] + "\n"
    assert "setpts=(PTS-STARTPTS)/4" in graph
    assert "concat=n=3:v=1:a=0" in graph
    assert "amix=inputs=4:normalize=0" in graph
    assert "loudnorm=I=-16:TP=-1.5:LRA=11" in graph
    assert "adelay=0:all=1" in graph                       # intro at t=0
    assert len([a for a in log["audio_inputs"]]) == 4


def test_later_narration_is_shifted_earlier_in_the_final_audio(built):
    # ask narration: source 6.5 s -> remapped 6.5 - 2.55 = 3.95 s ; open ends at 2.5 s
    ends = silence_ends(built["final"])
    assert any(abs(e - 3.95) < 0.2 for e in ends), ends


def test_build_crops_a_larger_capture_to_the_viewport(tmp_path):
    markers = {"capture_start_epoch": 1.0, "intro_t": 0.0, "outro_t": 1.5, "end_t": 2.5,
               "steps": [{"id": "open", "t_start": 1.0, "t_end": 1.5, "status": "PASS",
                          "wait_windows": []}]}
    durations = {"intro": 1.0, "open": 0.5, "outro": 1.0}
    out = make_project(tmp_path / "out", capture_seconds=4.0, size="800x450",
                       markers=markers, durations=durations)
    final = edit.build(out, SCENARIO)
    info = ff.media_info(final)
    assert (info["width"], info["height"]) == (640, 360)
    log = json.loads((out / "logs" / "edit.json").read_text(encoding="utf-8"))
    assert log["crop"] == {"width": 640, "height": 360, "x": 0, "y": 0}
    assert "crop=640:360:0:0" in log["filter_complex"]
    assert abs(info["duration"] - log["plan"]["total"]) <= 0.3


def test_build_skips_missing_segments_and_notes_it(tmp_path):
    out = make_project(tmp_path / "out", capture_seconds=12.0)
    (out / "audio" / "seg-ask.wav").unlink()
    final = edit.build(out, SCENARIO)
    assert final.is_file()
    log = json.loads((out / "logs" / "edit.json").read_text(encoding="utf-8"))
    assert any("ask" in n for n in log["notes"])
    assert len(log["audio_inputs"]) == 3


def test_build_clamps_a_timeline_longer_than_the_capture(tmp_path):
    out = make_project(tmp_path / "out", capture_seconds=6.0)     # markers end at 9.0
    final = edit.build(out, SCENARIO)
    log = json.loads((out / "logs" / "edit.json").read_text(encoding="utf-8"))
    assert any("clamped" in n for n in log["notes"])
    assert log["plan"]["map"]["source_end"] == pytest.approx(6.0, abs=0.05)
    assert abs(ff.media_info(final)["duration"] - log["plan"]["total"]) <= 0.3


def test_build_without_capture_is_a_one_line_error(tmp_path):
    out = tmp_path / "out"
    (out / "logs").mkdir(parents=True)
    (out / "logs" / "markers.json").write_text(json.dumps(MARKERS), encoding="utf-8")
    with pytest.raises(edit.EditError, match="run `record` first") as ei:
        edit.build(out, SCENARIO)
    assert "\n" not in str(ei.value)


def test_build_without_markers_is_a_one_line_error(tmp_path):
    out = tmp_path / "out"
    make_video(out / "raw" / "capture.mp4", 2.0)
    with pytest.raises(edit.EditError, match="markers.json"):
        edit.build(out, SCENARIO)


def test_build_without_narration_is_a_one_line_error(tmp_path):
    out = make_project(tmp_path / "out", capture_seconds=12.0)
    for wav in (out / "audio").glob("seg-*.wav"):
        wav.unlink()
    with pytest.raises(edit.EditError, match="run `synth` first"):
        edit.build(out, SCENARIO)


def test_durations_fall_back_to_measuring_wavs(tmp_path):
    out = make_project(tmp_path / "out", capture_seconds=12.0)
    (out / "audio" / "durations.json").unlink()
    d = edit.load_durations(out, ["intro", "open", "ask", "outro", "nope"])
    assert d["intro"] == pytest.approx(1.5, abs=0.01)
    assert d["ask"] == pytest.approx(1.5, abs=0.01)
    assert d["nope"] == 0.0


def test_amix_tree_chunks_many_inputs():
    lines: list[str] = []
    labels = [f"[a{i}]" for i in range(70)]
    bus = edit._amix_tree(labels, lines)
    assert bus.startswith("[mix1_")
    counts = [int(m) for m in re.findall(r"amix=inputs=(\d+)", "\n".join(lines))]
    assert counts == [32, 32, 6, 3]


def test_crop_for_only_when_capture_is_larger():
    vp = {"width": 640, "height": 360}
    assert edit.crop_for({"width": 640, "height": 360}, vp) is None
    assert edit.crop_for({"width": 320, "height": 180}, vp) is None
    assert edit.crop_for({"width": 641, "height": 360}, vp) == {"width": 640, "height": 360, "x": 0, "y": 0}
    assert edit.crop_for({"width": 800, "height": 450}, None) is None
