"""``ffmpeg_util``: discovery order, stderr parsers (pure) and live filters on lavfi media."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from demo_smoke import ffmpeg_util as ff

BANNER = """Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'final/demo.mp4':
  Metadata:
    major_brand     : isom
  Duration: 00:01:23.45, start: 0.000000, bitrate: 1234 kb/s
  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), yuv420p(progressive), 1920x1080 [SAR 1:1 DAR 16:9], 1100 kb/s, 30 fps, 30 tbr, 15360 tbn (default)
  Stream #0:1[0x2](und): Audio: aac (LC) (mp4a / 0x6134706D), 48000 Hz, stereo, fltp, 128 kb/s (default)
At least one output file must be specified
"""

BLACK_LOG = """[blackdetect @ 0x7f] black_start:0 black_end:0.966667 black_duration:0.966667
[blackdetect @ 0x7f] black_start:2.5 black_end:3.1 black_duration:0.6
"""

VOLUME_LOG = """[Parsed_volumedetect_0 @ 0x2ab] n_samples: 0
[Parsed_volumedetect_0 @ 0x7fc] n_samples: 577536
[Parsed_volumedetect_0 @ 0x7fc] mean_volume: -19.6 dB
[Parsed_volumedetect_0 @ 0x7fc] max_volume: -10.5 dB
[Parsed_volumedetect_0 @ 0x7fc] histogram_10db: 8
"""


# --------------------------------------------------------------------------- discovery


def test_find_ffmpeg_default_order(monkeypatch):
    monkeypatch.delenv("DEMO_SMOKE_FFMPEG", raising=False)
    exe = ff.find_ffmpeg()
    assert Path(exe).is_file()
    import shutil

    on_path = shutil.which("ffmpeg")
    if on_path:
        assert exe == on_path
    else:
        import imageio_ffmpeg

        assert exe == imageio_ffmpeg.get_ffmpeg_exe()


def test_find_ffmpeg_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("DEMO_SMOKE_FFMPEG", str(tmp_path / "missing"))
    with pytest.raises(ff.FfmpegError, match="DEMO_SMOKE_FFMPEG"):
        ff.find_ffmpeg()
    monkeypatch.delenv("DEMO_SMOKE_FFMPEG")
    real = ff.find_ffmpeg()
    monkeypatch.setenv("DEMO_SMOKE_FFMPEG", real)
    assert ff.find_ffmpeg() == str(Path(real))
    assert ff.argv(["-i", "x"])[:4] == [str(Path(real)), "-hide_banner", "-nostdin", "-i"]


def test_find_ffmpeg_nothing_available(monkeypatch, tmp_path):
    monkeypatch.delenv("DEMO_SMOKE_FFMPEG", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    import imageio_ffmpeg

    monkeypatch.setattr(imageio_ffmpeg, "get_ffmpeg_exe", lambda: (_ for _ in ()).throw(RuntimeError("no")))
    with pytest.raises(ff.FfmpegError, match="ffmpeg not found") as ei:
        ff.find_ffmpeg()
    assert "\n" not in str(ei.value)


# --------------------------------------------------------------------------- parsers


def test_parse_info_banner():
    info = ff.parse_info(BANNER)
    assert info["duration"] == pytest.approx(83.45)
    assert (info["width"], info["height"]) == (1920, 1080)
    assert info["fps"] == 30.0 and info["video_codec"] == "h264"
    assert info["has_audio"] and info["audio_codec"] == "aac" and info["sample_rate"] == 48000
    assert info["audio_duration"] == pytest.approx(83.45)


def test_parse_info_video_only_and_empty():
    only_video = "\n".join(ln for ln in BANNER.splitlines() if "Audio:" not in ln)
    info = ff.parse_info(only_video)
    assert not info["has_audio"] and info["audio_duration"] is None and info["width"] == 1920
    empty = ff.parse_info("")
    assert empty["duration"] == 0.0 and empty["width"] == 0 and not empty["has_audio"]


def test_parse_blackdetect_and_volumedetect():
    assert ff.parse_blackdetect(BLACK_LOG) == [
        {"start": 0.0, "end": 0.966667, "duration": 0.966667},
        {"start": 2.5, "end": 3.1, "duration": 0.6}]
    assert ff.parse_blackdetect("nothing here") == []
    assert ff.parse_volumedetect(VOLUME_LOG) == {"mean_volume": -19.6, "max_volume": -10.5}
    assert ff.parse_volumedetect("n_samples: 0") == {"mean_volume": None, "max_volume": None}


def test_parse_last_time():
    log = "frame=1 time=00:00:01.00 x\nframe=99 time=00:01:02.53 y\n"
    assert ff.parse_last_time(log) == pytest.approx(62.53)
    assert ff.parse_last_time("time=N/A") is None


# --------------------------------------------------------------------------- live


@pytest.fixture(scope="module")
def clips(tmp_path_factory) -> dict:
    d = tmp_path_factory.mktemp("clips")
    color = d / "testsrc.mp4"
    ff.run(["-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=440", "-t", "2", "-c:v", "libx264",
            "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", str(color)])
    black = d / "black.mp4"
    ff.run(["-y", "-f", "lavfi", "-i", "color=black:size=320x180:rate=30:duration=2",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(black)])
    return {"color": color, "black": black, "dir": d}


def test_media_info_live(clips):
    info = ff.media_info(clips["color"])
    assert info["duration"] == pytest.approx(2.0, abs=0.1)
    assert (info["width"], info["height"]) == (320, 180)
    assert info["has_audio"] and info["sample_rate"] == 48000
    mute = ff.media_info(clips["black"])
    assert not mute["has_audio"]


def test_media_info_errors(tmp_path):
    with pytest.raises(ff.FfmpegError, match="not found"):
        ff.media_info(tmp_path / "nope.mp4")
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"not a video")
    with pytest.raises(ff.FfmpegError, match="not a readable media file"):
        ff.media_info(junk)


def test_stream_duration_live(clips):
    assert ff.stream_duration(clips["color"], "video") == pytest.approx(2.0, abs=0.1)
    assert ff.stream_duration(clips["color"], "audio") == pytest.approx(2.0, abs=0.1)


def test_blackdetect_live(clips):
    assert ff.blackdetect(clips["color"], ss=0.0, t=1.0) == []
    black = ff.blackdetect(clips["black"], ss=0.0, t=1.0, d=0.1, pic_th=0.98)
    assert len(black) == 1 and black[0]["start"] == 0.0 and black[0]["duration"] > 0.8
    tail = ff.blackdetect(clips["black"], ss=1.0, t=1.0)
    assert len(tail) == 1 and tail[0]["start"] == pytest.approx(0.0, abs=0.05)


def test_volumedetect_live(clips):
    vol = ff.volumedetect(clips["color"])
    assert vol["mean_volume"] is not None and -25 < vol["mean_volume"] < 0
    assert vol["max_volume"] is not None and vol["max_volume"] >= vol["mean_volume"]
    assert ff.volumedetect(clips["black"]) == {"mean_volume": None, "max_volume": None}


def test_thumbnail_live(clips):
    dst = clips["dir"] / "sub" / "thumb.png"
    assert ff.thumbnail(clips["color"], 1.0, dst) == dst
    assert dst.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_run_failure_is_one_line(tmp_path):
    with pytest.raises(ff.FfmpegError, match=r"broken failed \(exit \d+\)") as ei:
        ff.run(["-i", str(tmp_path / "nope.mp4"), "-f", "null", "-"], what="broken")
    assert "\n" not in str(ei.value)
    cp = ff.run(["-i", str(tmp_path / "nope.mp4")], check=False)
    assert cp.returncode != 0 and cp.stderr


def test_run_never_uses_a_shell():
    weird = "a b;echo&x"
    cp = ff.run(["-i", weird], check=False)
    assert weird in cp.stderr or "No such file" in cp.stderr
    assert os.environ.get("DEMO_SMOKE_FFMPEG") is None or Path(os.environ["DEMO_SMOKE_FFMPEG"]).is_file()
