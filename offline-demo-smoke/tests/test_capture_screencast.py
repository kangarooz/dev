"""Capture backends: screencast end-to-end (headless), screen grabber argv per OS, record() pacing."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from demo_smoke import capture, chrome, drive

KIT = Path(__file__).resolve().parents[1]
APP_DIR = KIT / "tests" / "fixtures" / "app"
SCEN_DIR = KIT / "tests" / "fixtures" / "scenarios"
CHROME_DEFAULT = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"


def _env() -> None:
    if "DEMO_SMOKE_CHROME" not in os.environ and Path(CHROME_DEFAULT).exists():
        os.environ["DEMO_SMOKE_CHROME"] = CHROME_DEFAULT
    if "DEMO_SMOKE_FFMPEG" not in os.environ:
        import imageio_ffmpeg

        os.environ["DEMO_SMOKE_FFMPEG"] = imageio_ffmpeg.get_ffmpeg_exe()


def _serve_dir():
    try:
        from tests.fixtures.serve import serve_dir
    except ImportError:
        spec = importlib.util.spec_from_file_location("fixture_serve", KIT / "tests" / "fixtures" / "serve.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        serve_dir = mod.serve_dir
    return serve_dir


def _need_chrome() -> None:
    _env()
    if not chrome.find_chrome():
        pytest.skip("no Chrome binary available (set DEMO_SMOKE_CHROME)")


def media_duration(path: Path) -> float:
    """demo_smoke.env.media_info when present, else parse ``ffmpeg -i``."""
    try:
        from demo_smoke.env import media_info

        return float(media_info(path)["duration"])
    except ImportError:
        pass
    proc = subprocess.run([capture.find_ffmpeg(), "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                          check=False)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr)
    assert m, proc.stderr
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


# --------------------------------------------------------------------------- pure
@pytest.mark.parametrize("os_name", ["Windows", "Darwin", "Linux"])
def test_grab_args_per_os(os_name, tmp_path):
    out = tmp_path / "capture.mp4"
    bounds = {"x": 10, "y": 20, "width": 1281, "height": 721}
    argv = capture.grab_args("ffmpeg.exe", os_name, bounds, 30, out, display=":0")
    assert argv[0] == "ffmpeg.exe" and argv[-1] == str(out)
    assert argv[argv.index("-c:v") + 1] == "libx264"
    assert argv[argv.index("-preset") + 1] == "ultrafast"
    assert argv[argv.index("-crf") + 1] == "18"
    assert argv[argv.index("-framerate") + 1] == "30"
    if os_name == "Windows":
        assert argv[argv.index("-f") + 1] == "gdigrab"
        assert argv[argv.index("-i") + 1] == "desktop"
        assert argv[argv.index("-offset_x") + 1] == "10" and argv[argv.index("-offset_y") + 1] == "20"
        assert argv[argv.index("-video_size") + 1] == "1280x720"  # even sizes for yuv420p
    elif os_name == "Darwin":
        assert argv[argv.index("-f") + 1] == "avfoundation"
        assert argv[argv.index("-i") + 1] == "1:none"
        assert argv[argv.index("-vf") + 1] == "crop=1280:720:10:20"
    else:
        assert argv[argv.index("-f") + 1] == "x11grab"
        assert argv[argv.index("-i") + 1] == ":0.0+10,20"
        assert argv[argv.index("-video_size") + 1] == "1280x720"


def test_screen_capture_uses_window_bounds_and_refuses_headless(tmp_path):
    session = SimpleNamespace(viewport={"width": 1280, "height": 720},
                              window_bounds={"x": 0, "y": 0, "width": 1280, "height": 808}, headless=True)
    cap = capture.make("screen", session, tmp_path)
    assert isinstance(cap, capture.ScreenCapture)
    argv = cap.args(ffmpeg="ffmpeg", os_name="Linux")
    assert argv[argv.index("-video_size") + 1] == "1280x808"
    with pytest.raises(capture.CaptureError, match="screencast"):
        cap.start()
    with pytest.raises(capture.CaptureError, match="unknown capture backend"):
        capture.make("webcam", session, tmp_path)


def test_concat_list_repeats_last_frame(tmp_path):
    session = SimpleNamespace(viewport={"width": 640, "height": 360}, cdp=None, page=None, headless=True)
    cap = capture.ScreencastCapture(session, tmp_path)
    cap.frames = [("000000.jpg", 0.25), ("000001.jpg", 1.0), ("000002.jpg", 1.5)]
    cap.t_stop = 3.0
    text = cap.concat_list()
    assert text.startswith("ffconcat version 1.0\n")
    durations = [float(x) for x in re.findall(r"^duration (\S+)$", text, re.MULTILINE)]
    assert durations == pytest.approx([1.0, 0.5, 1.5])  # first frame is shown from t=0
    assert text.rstrip().endswith("file 'frames/000002.jpg'")
    vf = cap.ffmpeg_args("ffmpeg")
    assert "fps=30" in vf[vf.index("-vf") + 1] and vf[vf.index("-crf") + 1] == "18"


# --------------------------------------------------------------------------- browser
def test_screencast_capture_two_seconds(tmp_path):
    _need_chrome()
    with _serve_dir()(APP_DIR) as base, chrome.launch(tmp_path, {"width": 960, "height": 540}, headless=True) as session:
        page = session.page
        page.goto(base + "/index.html", wait_until="load")
        cap = capture.make("screencast", session, tmp_path)
        assert isinstance(cap, capture.ScreencastCapture)
        cap.start()
        assert cap.now() < 0.5
        flip = 0
        while cap.now() < 2.0:
            flip += 1
            page.goto(base + ("/login.html" if flip % 2 else "/index.html"), wait_until="load")
            page.mouse.move(100 + flip * 40, 200, steps=5)
            page.wait_for_timeout(150)
        path = cap.stop()
        assert cap.capture_start_epoch is not None
    assert path == tmp_path / "raw" / "capture.mp4" and path.exists() and path.stat().st_size > 1000
    assert len(cap.frames) >= 2 and cap.note == ""
    frames = json.loads((tmp_path / "raw" / "frames.json").read_text(encoding="utf-8"))
    assert frames["frame_count"] == len(cap.frames)
    assert all((tmp_path / "raw" / f["file"]).exists() for f in frames["frames"])
    assert (tmp_path / "raw" / "frames.txt").exists()
    assert 1.7 <= media_duration(path) <= 2.7


def test_record_writes_capture_and_markers(tmp_path):
    _need_chrome()
    path = SCEN_DIR / "fixture-pass.json"
    scenario = json.loads(path.read_text(encoding="utf-8"))
    scenario["_dir"] = path.parent
    durations = {"intro": 0.6, "open": 0.4, "upload": 0.4, "ask": 0.5, "outro": 0.4}
    with _serve_dir()(APP_DIR) as base:
        scenario["app_url"] = base
        markers = drive.record(scenario, tmp_path, "screencast", True, durations)

    assert (tmp_path / "raw" / "capture.mp4").exists()
    assert (tmp_path / "logs" / "markers.json").exists()
    assert markers["intro_t"] == 0.0
    ids = [s["id"] for s in markers["steps"]]
    assert ids == ["open", "upload", "ask"]
    assert all(s["status"] == "PASS" for s in markers["steps"])
    starts = [s["t_start"] for s in markers["steps"]]
    assert starts[0] >= 0.6, "first step waits for the intro"
    assert starts == sorted(starts)
    for prev, nxt in zip(markers["steps"], markers["steps"][1:]):
        assert nxt["t_start"] >= prev["t_end"] + 0.3 - 0.05
    last = markers["steps"][-1]
    assert markers["outro_t"] >= last["t_end"]
    assert markers["end_t"] == pytest.approx(markers["outro_t"] + 0.4, abs=0.01)
    assert (tmp_path / "logs" / "record-01-open.png").exists()
    video_s = media_duration(tmp_path / "raw" / "capture.mp4")
    assert abs(video_s - (markers["end_t"] + 2.0)) <= 0.6
