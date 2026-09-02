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


def _env() -> None:
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
        # the screen is addressed by name: index 0 = main display regardless of attached cameras
        assert argv[argv.index("-i") + 1] == "Capture screen 0:none"
        assert argv[argv.index("-vf") + 1] == "crop=1280:720:10:20"
    else:
        assert argv[argv.index("-f") + 1] == "x11grab"
        assert argv[argv.index("-i") + 1] == ":0.0+10,20"
        assert argv[argv.index("-video_size") + 1] == "1280x720"
    assert "-vf" not in argv or os_name == "Darwin"          # no rescale at scale 1.0


@pytest.mark.parametrize("os_name", ["Windows", "Darwin", "Linux"])
def test_grab_args_scale_hidpi(os_name, tmp_path):
    """Retina (2x) / Windows 125% scaling: grab in physical pixels, scale back to the viewport."""
    out = tmp_path / "capture.mp4"
    bounds = {"x": 10, "y": 88, "width": 1280, "height": 720}
    argv = capture.grab_args("ffmpeg", os_name, bounds, 30, out, screen_index=1, scale=2.0)
    if os_name == "Windows":
        assert argv[argv.index("-offset_x") + 1] == "20" and argv[argv.index("-offset_y") + 1] == "176"
        assert argv[argv.index("-video_size") + 1] == "2560x1440"
        assert argv[argv.index("-vf") + 1] == "scale=1280:720:flags=bicubic"
    elif os_name == "Darwin":
        assert argv[argv.index("-i") + 1] == "Capture screen 1:none"
        assert argv[argv.index("-vf") + 1] == "crop=2560:1440:20:176,scale=1280:720:flags=bicubic"
    else:
        assert argv[argv.index("-i") + 1] == ":0.0+20,176"
        assert argv[argv.index("-video_size") + 1] == "2560x1440"
        assert argv[argv.index("-vf") + 1] == "scale=1280:720:flags=bicubic"
    argv = capture.grab_args("ffmpeg", "Windows", bounds, 30, out, scale=1.25)
    assert argv[argv.index("-video_size") + 1] == "1600x900"
    assert argv[argv.index("-offset_y") + 1] == "110"


def test_page_bounds_excludes_browser_ui():
    session = SimpleNamespace(viewport={"width": 1280, "height": 720},
                              window_bounds={"x": 0, "y": 0, "width": 1280, "height": 859},
                              ui_insets={"x": 0, "y": 139})
    assert capture.page_bounds(session) == {"x": 0, "y": 139, "width": 1280, "height": 720}
    legacy = SimpleNamespace(viewport={"width": 1280, "height": 720},
                             window_bounds={"x": 5, "y": 7, "width": 1280, "height": 808})
    assert capture.page_bounds(legacy) == {"x": 5, "y": 7, "width": 1280, "height": 808}


def test_screen_capture_uses_page_area_and_refuses_headless(tmp_path, monkeypatch):
    session = SimpleNamespace(viewport={"width": 1280, "height": 720},
                              window_bounds={"x": 0, "y": 0, "width": 1280, "height": 808},
                              ui_insets={"x": 0, "y": 88}, device_scale_factor=1.0, headless=True)
    monkeypatch.setenv("DEMO_SMOKE_SCREEN_INDEX", "2")
    cap = capture.make("screen", session, tmp_path)
    assert isinstance(cap, capture.ScreenCapture)
    argv = cap.args(ffmpeg="ffmpeg", os_name="Linux")
    assert argv[argv.index("-video_size") + 1] == "1280x720"        # viewport, not the outer window
    assert argv[argv.index("-i") + 1] == ":0.0+0,88"                 # offset by the tab strip/toolbar
    assert cap.args(ffmpeg="ffmpeg", os_name="Darwin")[cap.args(ffmpeg="ffmpeg", os_name="Darwin").index("-i") + 1] \
        == "Capture screen 2:none"
    with pytest.raises(capture.CaptureError, match="screencast"):
        cap.start()
    with pytest.raises(capture.CaptureError, match="unknown capture backend"):
        capture.make("webcam", session, tmp_path)


def test_screen_capture_abort_stops_grabber(tmp_path):
    """The error path must end the ffmpeg child and close its log handle."""
    import sys

    session = SimpleNamespace(viewport={"width": 64, "height": 64}, headless=False)
    cap = capture.ScreenCapture(session, tmp_path)
    cap._log = open(cap.log_path, "wb")  # noqa: SIM115 - mirrors start()
    cap._proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"],
                                 stdin=subprocess.PIPE, stdout=cap._log, stderr=subprocess.STDOUT)
    assert cap._proc.poll() is None
    cap.abort()
    assert cap._proc.poll() is not None and cap._log.closed and cap._stopped
    cap.abort()   # idempotent


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


def test_concat_list_keeps_real_timeline_through_bursts(tmp_path):
    """Regression: a 1/60 s minimum per frame made bursts of near-simultaneous frames push
    every later frame behind its real time (0.25 s over 8 s), so the narration led the picture."""
    session = SimpleNamespace(viewport={"width": 640, "height": 360}, cdp=None, page=None, headless=True)
    cap = capture.ScreencastCapture(session, tmp_path)
    frames = [("000000.jpg", 0.05)]
    t = 0.05
    for i in range(1, 400):                      # 400 frames: bursts 2 ms apart, then a normal gap
        t += 0.002 if i % 4 else 0.1
        frames.append((f"{i:06d}.jpg", t))
    cap.frames = frames
    cap.t_stop = t + 0.5
    text = cap.concat_list()
    durations = [float(x) for x in re.findall(r"^duration (\S+)$", text, re.MULTILINE)]
    names = re.findall(r"^file 'frames/(\S+)'$", text, re.MULTILINE)
    kept = cap.kept_frames()
    assert len(kept) < len(frames) and kept[0] == frames[0]
    assert frames[-1][1] - kept[-1][1] < 1.0 / 30 / 2          # only a sub-half-frame tail is dropped
    assert names[:-1] == [n for n, _ in kept] and names[-1] == names[-2]
    assert min(durations[:-1]) >= 1.0 / 30 / 2 - 1e-9          # bursts merged, no sub-half-frame entries
    # cumulative concat time of every kept frame equals its real timestamp; total equals t_stop
    placed = 0.0
    for (name, real_t), d in zip(kept, durations):
        if name != kept[0][0]:
            assert placed == pytest.approx(real_t, abs=1e-5), name
        placed += d
    assert placed == pytest.approx(cap.t_stop, abs=1e-5)


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


def test_record_aborts_capture_on_error(tmp_path, monkeypatch):
    """An exception between cap.start() and cap.stop() must not leave the capture running."""
    _need_chrome()
    path = SCEN_DIR / "fixture-pass.json"
    scenario = json.loads(path.read_text(encoding="utf-8"))
    scenario["_dir"] = path.parent
    seen: dict = {}
    real_make = capture.make

    def make(kind, session, out):
        seen["cap"] = real_make(kind, session, out)
        return seen["cap"]

    def boom(*a, **kw):
        raise RuntimeError("step executor crashed")

    monkeypatch.setattr(capture, "make", make)
    monkeypatch.setattr(drive, "run_steps", boom)
    with _serve_dir()(APP_DIR) as base:
        scenario["app_url"] = base
        with pytest.raises(RuntimeError, match="step executor crashed"):
            drive.record(scenario, tmp_path, "screencast", True, {"intro": 0.2})
    cap = seen["cap"]
    assert cap._stopped is True
    assert (tmp_path / "raw" / "frames.json").exists()          # abort() still writes the index
    assert not (tmp_path / "logs" / "markers.json").exists()


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
    assert markers["verdict"] == "PASS" and "error" not in markers
    assert markers["device_scale_factor"] >= 1.0 and "y" in markers["ui_insets"]
    assert (tmp_path / "logs" / "record-01-open.png").exists()
    video_s = media_duration(tmp_path / "raw" / "capture.mp4")
    # the concat timeline is anchored to real timestamps: no cumulative drift from frame bursts
    assert abs(video_s - (markers["end_t"] + 2.0)) <= 0.2
    assert markers["capture_seconds"] == pytest.approx(video_s, abs=0.2)     # capture length, not wall time
    frames = json.loads((tmp_path / "raw" / "frames.json").read_text(encoding="utf-8"))
    assert markers["capture_seconds"] == pytest.approx(frames["t_stop"], abs=0.001)


def _jpeg_size(path: Path) -> tuple[int, int]:
    """(width, height) from the JPEG SOF marker; no image library needed."""
    data = path.read_bytes()
    i = 2
    while i < len(data) - 9:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
        i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    raise AssertionError(f"no SOF marker in {path}")


def test_screencast_frames_match_viewport(tmp_path):
    """Regression: Playwright's viewport emulation lives on *its* CDP session, so the
    screencast on our session used to capture the raw --headless=new window area
    (1280x580 for a 1280x720 window) and the assembler padded the rest.  The
    fixture app plus the cursor overlay (which forces repaints) reproduced it."""
    from demo_smoke import cursor

    _need_chrome()
    viewport = {"width": 1280, "height": 720}
    with _serve_dir()(APP_DIR) as base, chrome.launch(tmp_path, viewport, headless=True) as session:
        cursor.install(session.cdp)
        page = session.page
        page.goto(base + "/index.html", wait_until="load")
        page.wait_for_timeout(200)
        cap = capture.make("screencast", session, tmp_path)
        cap.start()
        for i in range(6):
            page.mouse.move(150 + i * 120, 200 + i * 40, steps=4)
            page.wait_for_timeout(150)
        cap.stop()
    assert cap.note == "", cap.note
    assert len(cap.frames) >= 2
    frames = json.loads((tmp_path / "raw" / "frames.json").read_text(encoding="utf-8"))
    assert frames["frame_sizes"] == {"1280x720": len(cap.frames)}, frames["frame_sizes"]
    sizes = {_jpeg_size(tmp_path / "raw" / f["file"]) for f in frames["frames"]}
    assert sizes == {(1280, 720)}, sizes
