"""Video capture backends.

``ScreencastCapture`` (default, works headless and without a display) uses CDP
``Page.startScreencast``; frames are written to ``raw/frames/NNNNNN.jpg`` with
monotonic timestamps and assembled into ``raw/capture.mp4`` on ``stop()`` with
the ffmpeg concat demuxer at 30 fps CFR.

``ScreenCapture`` runs an OS screen grabber (gdigrab / avfoundation / x11grab)
over the Chrome window bounds; it needs a real display.

Both expose ``start() -> t0``, ``now() -> seconds since t0``, ``stop() -> Path``.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

JPEG_QUALITY = 80


class CaptureError(RuntimeError):
    """Capture could not start or ffmpeg failed (one-line message)."""


def find_ffmpeg() -> str:
    """``DEMO_SMOKE_FFMPEG`` -> ``demo_smoke.env.find_ffmpeg`` -> PATH -> imageio-ffmpeg."""
    env_path = os.environ.get("DEMO_SMOKE_FFMPEG")
    if env_path:
        return env_path
    try:
        # lazy: env.py is another builder's module
        from demo_smoke.env import find_ffmpeg as env_find
    except ImportError:
        log.debug("ignored error", exc_info=True)
        env_find = None
    if env_find is not None:
        try:
            return str(env_find())
        except (RuntimeError, OSError):
            log.debug("env.find_ffmpeg failed, trying PATH", exc_info=True)
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError, OSError) as exc:
        raise CaptureError("ffmpeg not found: install ffmpeg, pip install imageio-ffmpeg, or set DEMO_SMOKE_FFMPEG") from exc


def _paths(out: Path):
    """Use ``demo_smoke.env.Paths`` when available, else create the same layout."""
    out = Path(out)
    try:
        from demo_smoke.env import Paths

        return Paths(out)
    except ImportError:
        pass  # env.py not present: create the same layout ourselves

    class _P:
        pass

    p = _P()
    for name in ("raw", "audio", "clips", "final", "logs"):
        d = out / name
        d.mkdir(parents=True, exist_ok=True)
        setattr(p, name, d)
    return p


def _even(n: int) -> int:
    n = int(n)
    return n if n % 2 == 0 else n - 1


def _run_ffmpeg(argv: list[str], log_path: Path) -> None:
    try:
        proc = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace", check=False)
    except OSError as exc:
        raise CaptureError(f"could not run ffmpeg ({argv[0]}): {exc}") from None
    try:
        log_path.write_text(" ".join(argv) + "\n\n" + (proc.stderr or ""), encoding="utf-8")
    except OSError:
        pass
    if proc.returncode != 0:
        lines = [ln for ln in (proc.stderr or "").splitlines() if ln.strip()]
        detail = lines[-1].strip() if lines else f"exit code {proc.returncode}"
        raise CaptureError(f"ffmpeg failed while assembling the capture: {detail}")


class ScreencastCapture:
    """CDP screencast -> JPEG frames -> ``raw/capture.mp4`` (30 fps, H.264)."""

    def __init__(self, session, out: Path, fps: int = 30):
        self.session = session
        self.out = Path(out)
        self.fps = int(fps)
        self.paths = _paths(self.out)
        self.frames_dir = self.paths.raw / "frames"
        self.list_path = self.paths.raw / "frames.txt"
        self.index_path = self.paths.raw / "frames.json"
        self.path = self.paths.raw / "capture.mp4"
        self.viewport = {"width": int(session.viewport["width"]), "height": int(session.viewport["height"])}
        self.frames: list[tuple[str, float]] = []  # (relative file name, seconds since t0)
        self.note: str = ""
        self.t0: float | None = None
        self.t_stop: float | None = None
        self.capture_start_epoch: float | None = None
        self._started = False
        self._stopped = False
        self._handler = None
        self._dropped = 0

    # -- CDP plumbing -------------------------------------------------------
    def _on_frame(self, event: dict) -> None:
        cdp = self.session.cdp
        session_id = event.get("sessionId")
        try:
            if self._started and not self._stopped and self.t0 is not None:
                t = time.monotonic() - self.t0
                index = len(self.frames)
                name = f"{index:06d}.jpg"
                (self.frames_dir / name).write_bytes(base64.b64decode(event["data"]))
                self.frames.append((name, t))
        except Exception:
            log.debug("ignored error", exc_info=True)
            self._dropped += 1
        finally:
            if session_id is not None:
                try:
                    cdp.send("Page.screencastFrameAck", {"sessionId": session_id})
                except Exception:
                    log.debug("ignored error", exc_info=True)

    def start(self) -> float:
        if self._started:
            return self.t0
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        cdp = self.session.cdp
        self._handler = self._on_frame
        cdp.on("Page.screencastFrame", self._handler)
        self.t0 = time.monotonic()
        self.capture_start_epoch = time.time()
        self._started = True
        try:
            cdp.send("Page.startScreencast", {
                "format": "jpeg",
                "quality": JPEG_QUALITY,
                "maxWidth": self.viewport["width"],
                "maxHeight": self.viewport["height"],
                "everyNthFrame": 1,
            })
        except Exception as exc:
            log.debug("handled error", exc_info=True)
            self._started = False
            raise CaptureError(f"Page.startScreencast failed: {_one_line(exc)}") from exc
        return self.t0

    def now(self) -> float:
        if self.t0 is None:
            return 0.0
        return time.monotonic() - self.t0

    def stop(self) -> Path:
        if self._stopped:
            return self.path
        if not self._started:
            raise CaptureError("screencast capture was never started")
        cdp = self.session.cdp
        page = self.session.page
        try:
            cdp.send("Page.stopScreencast")
        except Exception:
            log.debug("ignored error", exc_info=True)
        try:
            page.wait_for_timeout(50)  # let in-flight frames land
        except Exception:
            log.debug("ignored error", exc_info=True)
        self._stopped = True
        self.t_stop = time.monotonic() - self.t0
        try:
            cdp.remove_listener("Page.screencastFrame", self._handler)
        except Exception:
            log.debug("ignored error", exc_info=True)
        if len(self.frames) < 2:
            self._synthesize_still()
        self._write_index()
        self._assemble()
        return self.path

    # -- assembly -----------------------------------------------------------
    def _synthesize_still(self) -> None:
        """Fewer than two frames arrived: fall back to a page screenshot held for the whole capture."""
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        still = self.frames_dir / "still.jpg"
        try:
            self.session.page.screenshot(path=str(still), type="jpeg", quality=JPEG_QUALITY)
        except Exception as exc:
            log.debug("handled error", exc_info=True)
            self.note = f"no screencast frames and screenshot failed ({_one_line(exc)})"
            raise CaptureError("screencast produced no frames and a fallback screenshot failed") from exc
        got = len(self.frames)
        self.frames = [(still.name, 0.0), (still.name, max(self.t_stop or 0.0, 0.1))]
        self.note = f"only {got} screencast frame(s) arrived; video synthesized from a still page screenshot"

    def _write_index(self) -> None:
        data = {
            "fps": self.fps,
            "viewport": self.viewport,
            "capture_start_epoch": self.capture_start_epoch,
            "t_stop": self.t_stop,
            "frame_count": len(self.frames),
            "dropped": self._dropped,
            "note": self.note,
            "frames": [{"file": f"frames/{name}", "t": round(t, 4)} for name, t in self.frames],
        }
        try:
            self.index_path.write_text(json.dumps(data, indent=1), encoding="utf-8")
        except OSError:
            pass

    def concat_list(self) -> str:
        """The ffconcat list: every frame is shown until the next one; the last frame is repeated to the stop time."""
        total = max(self.t_stop or 0.0, 0.0)
        lines = ["ffconcat version 1.0"]
        n = len(self.frames)
        for i, (name, t) in enumerate(self.frames):
            start = 0.0 if i == 0 else t
            end = self.frames[i + 1][1] if i + 1 < n else max(total, t)
            duration = max(end - start, 1.0 / self.fps / 2)
            lines.append(f"file 'frames/{name}'")
            lines.append(f"duration {duration:.6f}")
        # ffmpeg only honours the duration of the last entry when it is followed by another entry.
        last_name = self.frames[-1][0]
        lines.append(f"file 'frames/{last_name}'")
        return "\n".join(lines) + "\n"

    def ffmpeg_args(self, ffmpeg: str) -> list[str]:
        w, h = _even(self.viewport["width"]), _even(self.viewport["height"])
        vf = (
            f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=bicubic,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"fps={self.fps}"
        )
        return [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            # Screencast frames can change size (e.g. the first frames); keep one
            # filtergraph alive so scale/pad absorb that instead of fps losing frames.
            "-reinit_filter", "0",
            "-f", "concat", "-safe", "0", "-i", str(self.list_path),
            "-vf", vf,
            "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(self.path),
        ]

    def _assemble(self) -> None:
        self.list_path.write_text(self.concat_list(), encoding="utf-8")
        _run_ffmpeg(self.ffmpeg_args(find_ffmpeg()), self.paths.logs / "ffmpeg-capture.log")


def grab_args(ffmpeg: str, os_name: str, bounds: dict, fps: int, out_path: Path,
              screen_index: int = 1, display: str | None = None) -> list[str]:
    """ffmpeg argv for the OS screen grabber (pure; ``os_name`` is Windows/Darwin/Linux)."""
    x, y = int(bounds.get("x", 0)), int(bounds.get("y", 0))
    w, h = _even(bounds.get("width", 1280)), _even(bounds.get("height", 720))
    encode = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p",
              "-movflags", "+faststart", str(out_path)]
    head = [ffmpeg, "-hide_banner", "-loglevel", "warning", "-y"]
    if os_name == "Windows":
        return head + [
            "-f", "gdigrab", "-framerate", str(fps), "-draw_mouse", "1",
            "-offset_x", str(x), "-offset_y", str(y), "-video_size", f"{w}x{h}",
            "-i", "desktop",
        ] + encode
    if os_name == "Darwin":
        return head + [
            "-f", "avfoundation", "-framerate", str(fps), "-capture_cursor", "1",
            "-i", f"{screen_index}:none",
            "-vf", f"crop={w}:{h}:{x}:{y}",
        ] + encode
    disp = display or os.environ.get("DISPLAY") or ":0"
    if "." not in disp.split(":")[-1]:
        disp = disp + ".0"
    return head + [
        "-f", "x11grab", "-framerate", str(fps), "-draw_mouse", "1",
        "-video_size", f"{w}x{h}", "-i", f"{disp}+{x},{y}",
    ] + encode


class ScreenCapture:
    """OS screen grabber over the Chrome window bounds (needs a display)."""

    def __init__(self, session, out: Path, fps: int = 30):
        self.session = session
        self.out = Path(out)
        self.fps = int(fps)
        self.paths = _paths(self.out)
        self.path = self.paths.raw / "capture.mp4"
        self.log_path = self.paths.logs / "screen-capture.log"
        self.bounds = dict(getattr(session, "window_bounds", None) or {"x": 0, "y": 0, **session.viewport})
        self.note = ""
        self.t0: float | None = None
        self.capture_start_epoch: float | None = None
        self._proc: subprocess.Popen | None = None
        self._log = None
        self._stopped = False

    def args(self, ffmpeg: str | None = None, os_name: str | None = None) -> list[str]:
        return grab_args(ffmpeg or find_ffmpeg(), os_name or platform.system(), self.bounds, self.fps, self.path)

    def start(self) -> float:
        if self._proc is not None:
            return self.t0
        if getattr(self.session, "headless", False):
            raise CaptureError("screen capture needs a visible window: use --capture screencast with --headless")
        argv = self.args()
        self._log = open(self.log_path, "wb")  # noqa: SIM115 - handed to Popen, closed in stop()
        try:
            self._proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=self._log, stderr=subprocess.STDOUT)
        except OSError as exc:
            self._log.close()
            raise CaptureError(f"could not start ffmpeg screen grabber: {exc}") from None
        self.t0 = time.monotonic()
        self.capture_start_epoch = time.time()
        # Give ffmpeg a moment; if it dies immediately the device is not usable.
        time.sleep(0.5)
        if self._proc.poll() is not None:
            self._log.close()
            raise CaptureError(f"ffmpeg screen grabber exited immediately: {self._log_tail()}")
        return self.t0

    def now(self) -> float:
        if self.t0 is None:
            return 0.0
        return time.monotonic() - self.t0

    def stop(self) -> Path:
        if self._stopped:
            return self.path
        self._stopped = True
        proc = self._proc
        if proc is None:
            raise CaptureError("screen capture was never started")
        try:
            if proc.stdin:
                proc.stdin.write(b"q\n")
                proc.stdin.flush()
                proc.stdin.close()
        except Exception:
            log.debug("ignored error", exc_info=True)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        if self._log:
            self._log.close()
        if proc.returncode not in (0, 255) or not self.path.exists():
            raise CaptureError(f"ffmpeg screen grabber failed (exit {proc.returncode}): {self._log_tail()}")
        return self.path

    def _log_tail(self) -> str:
        try:
            lines = [ln for ln in self.log_path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
            return lines[-1] if lines else "no output"
        except OSError:
            return "no log"


def make(kind: str, session, out: Path) -> ScreencastCapture | ScreenCapture:
    kind = (kind or "screencast").lower()
    if kind == "screencast":
        return ScreencastCapture(session, out)
    if kind == "screen":
        return ScreenCapture(session, out)
    raise CaptureError(f"unknown capture backend '{kind}' (expected screencast or screen)")


def _one_line(exc: BaseException) -> str:
    text = str(exc).strip()
    return text.splitlines()[0][:300] if text else exc.__class__.__name__
