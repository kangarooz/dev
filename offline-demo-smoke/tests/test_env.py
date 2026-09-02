import json
import os
import subprocess
from pathlib import Path

import pytest

from demo_smoke import env

FFMPEG_I_SAMPLE = """ffmpeg version 7.0.2-static https://johnvansickle.com/ffmpeg/  Copyright (c) 2000-2024 the FFmpeg developers
Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'final/demo.mp4':
  Metadata:
    major_brand     : isom
  Duration: 00:01:23.45, start: 0.000000, bitrate: 1234 kb/s
  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), yuv420p(progressive), 1920x1080 [SAR 1:1 DAR 16:9], 1100 kb/s, 30 fps, 30 tbr, 15360 tbn (default)
  Stream #0:1[0x2](und): Audio: aac (LC) (mp4a / 0x6134706D), 48000 Hz, stereo, fltp, 128 kb/s (default)
At least one output file must be specified
"""


def test_paths_creates_layout(out_dir):
    p = env.Paths(out_dir)
    for d in (p.raw, p.audio, p.clips, p.final, p.logs):
        assert d.is_dir()
    assert p.logs == out_dir / "logs"
    assert os.fspath(p) == str(out_dir)
    assert "Paths(" in repr(p)


def test_find_ffmpeg_and_version():
    ff = env.find_ffmpeg()
    assert Path(ff).is_file()
    assert env.ffmpeg_version(ff)


def test_find_ffmpeg_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("DEMO_SMOKE_FFMPEG", str(tmp_path / "nope"))
    with pytest.raises(RuntimeError, match="DEMO_SMOKE_FFMPEG"):
        env.find_ffmpeg()
    fake = tmp_path / "ffmpeg"
    fake.write_text("")
    monkeypatch.setenv("DEMO_SMOKE_FFMPEG", str(fake))
    assert env.find_ffmpeg() == str(fake)


def test_find_ffmpeg_missing_has_install_hint(monkeypatch):
    monkeypatch.delenv("DEMO_SMOKE_FFMPEG", raising=False)
    monkeypatch.setattr(env.shutil, "which", lambda name: None)
    monkeypatch.setitem(__import__("sys").modules, "imageio_ffmpeg", None)
    with pytest.raises(RuntimeError, match="ffmpeg not found"):
        env.find_ffmpeg()


def test_find_chrome(monkeypatch, tmp_path):
    monkeypatch.setenv("DEMO_SMOKE_CHROME", str(tmp_path / "nochrome"))
    assert env.find_chrome() is None
    monkeypatch.delenv("DEMO_SMOKE_CHROME")
    found = env.find_chrome()
    assert found is None or Path(found).is_file()


def test_find_ffprobe_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DEMO_SMOKE_FFPROBE", str(tmp_path / "nope"))
    assert env.find_ffprobe() is None


def test_torch_device_value():
    assert env.torch_device() in ("cuda", "rocm", "mps", "cpu", "none")


def test_parse_ffmpeg_i():
    info = env.parse_ffmpeg_i(FFMPEG_I_SAMPLE)
    assert info == {"duration": pytest.approx(83.45), "width": 1920, "height": 1080,
                    "has_audio": True, "audio_duration": pytest.approx(83.45)}
    only_audio = "  Duration: 00:00:02.50, start: 0\n  Stream #0:0: Audio: pcm_s16le, 24000 Hz, mono\n"
    assert env.parse_ffmpeg_i(only_audio) == {"duration": 2.5, "width": 0, "height": 0,
                                              "has_audio": True, "audio_duration": 2.5}
    assert env.parse_ffmpeg_i("garbage")["duration"] == 0.0


@pytest.fixture(scope="module")
def synthetic_media(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    ff = env.find_ffmpeg()
    mp4 = d / "clip.mp4"
    subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=30",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(mp4)], check=True)
    wav = d / "tone.wav"
    subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1.5",
                    "-ar", "24000", str(wav)], check=True)
    silent = d / "silent.mp4"
    subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=1:size=64x64:rate=10",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(silent)], check=True)
    return {"mp4": mp4, "wav": wav, "silent": silent}


def test_media_info_ffmpeg_fallback(synthetic_media, monkeypatch):
    monkeypatch.setattr(env, "find_ffprobe", lambda: None)
    info = env.media_info(synthetic_media["mp4"])
    assert info["width"] == 320 and info["height"] == 240
    assert info["has_audio"] is True
    assert info["duration"] == pytest.approx(2.0, abs=0.15)
    assert info["audio_duration"] == pytest.approx(2.0, abs=0.15)
    info = env.media_info(synthetic_media["wav"])
    assert info["width"] == 0 and info["has_audio"] is True
    assert info["duration"] == pytest.approx(1.5, abs=0.05)
    info = env.media_info(synthetic_media["silent"])
    assert info["has_audio"] is False and info["audio_duration"] is None
    assert info["width"] == 64


def test_media_info_prefers_ffprobe(synthetic_media, monkeypatch, tmp_path):
    fake = tmp_path / "ffprobe.py"
    fake.write_text("")
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)

        class CP:
            returncode = 0
            stdout = json.dumps({"format": {"duration": "9.5"},
                                 "streams": [{"codec_type": "video", "width": 100, "height": 50},
                                             {"codec_type": "audio", "duration": "9.4"}]})
            stderr = ""
        return CP()

    monkeypatch.setattr(env, "find_ffprobe", lambda: str(fake))
    monkeypatch.setattr(env, "_run", fake_run)
    info = env.media_info(synthetic_media["mp4"])
    assert info == {"duration": 9.5, "width": 100, "height": 50, "has_audio": True, "audio_duration": 9.4}
    assert calls[0][0] == str(fake) and "-show_streams" in calls[0]


def test_media_info_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="media file not found"):
        env.media_info(tmp_path / "nope.mp4")


def test_detect_never_crashes(monkeypatch, tmp_path, unreachable_url):
    monkeypatch.setenv("DEMO_SMOKE_CHROME", str(tmp_path / "nochrome"))
    monkeypatch.setenv("DEMO_SMOKE_FFMPEG", str(tmp_path / "noffmpeg"))
    rep = env.detect(unreachable_url, "some-model")
    assert rep["ffmpeg"] is None and rep["chrome"] is None
    assert rep["llm"]["reachable"] is False and rep["llm"]["tool_call"] is None
    assert any("DEMO_SMOKE_FFMPEG" in h for h in rep["hints"])
    assert any("Chrome" in h for h in rep["hints"])
    assert any("not reachable" in h for h in rep["hints"])
    json.dumps(rep)  # serializable


def test_detect_with_fake_llm(fake_llm):
    fake_llm.queue.append({"tool_calls": [{"name": "get_step_status", "arguments": {"step_id": "open"}}]})
    rep = env.detect(fake_llm.base_url, "fake-model")
    assert rep["ffmpeg"] and rep["llm"]["reachable"] is True
    assert rep["llm"]["tool_call"]["pass"] is True
    assert rep["torch_device"] == env.torch_device()
    assert isinstance(rep["chatterbox"], bool)
