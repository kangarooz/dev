"""Video capture backends.

``ScreencastCapture`` (default, works headless and without a display) uses CDP
``Page.startScreencast``; frames are written to ``raw/frames/NNNNNN.jpg`` with
monotonic timestamps and assembled into ``raw/capture.mp4`` on ``stop()`` with
the ffmpeg concat demuxer at 30 fps CFR.

``ScreenCapture`` runs an OS screen grabber (gdigrab / avfoundation / x11grab)
over the page area of the Chrome window (window bounds offset by the measured
browser-UI insets, scaled by the display's backing scale factor and scaled back
to the viewport size); it needs a real display.

Both expose ``start() -> t0``, ``now() -> seconds since t0``, ``stop() -> Path`` and
record ``t_stop`` (seconds since t0 when the capture ended).
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
        self.frame_sizes: dict[str, int] = {}  # "WxH" reported by Chrome -> frame count

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
                meta = event.get("metadata") or {}
                if meta.get("deviceWidth") and meta.get("deviceHeight"):
                    key = f"{int(meta['deviceWidth'])}x{int(meta['deviceHeight'])}"
                    self.frame_sizes[key] = self.frame_sizes.get(key, 0) + 1
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
        # Device-metrics emulation is per DevTools session.  Playwright's
        # set_viewport_size only emulates *its* session, so a screencast on this
        # session would otherwise capture the raw window content area (in
        # --headless=new that is --window-size minus ~140 px of browser UI, e.g.
        # 1280x580 for a 1280x720 viewport) and the assembler would pad the
        # missing rows.  Emulating the same viewport here makes every frame
        # exactly viewport-sized.
        try:
            cdp.send("Emulation.setDeviceMetricsOverride", {
                "width": self.viewport["width"],
                "height": self.viewport["height"],
                "deviceScaleFactor": 1,
                "mobile": False,
            })
        except Exception as exc:
            log.debug("handled error", exc_info=True)
            self.note = f"could not emulate the viewport on the capture session ({_one_line(exc)})"
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
            try:   # stop()/abort() early-return when not started: detach the handler here
                cdp.remove_listener("Page.screencastFrame", self._handler)
            except Exception:
                log.debug("ignored error", exc_info=True)
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

    def abort(self) -> None:
        """Stop the screencast and drop the listener without assembling a video; never raises."""
        if self._stopped or not self._started:
            self._stopped = True
            return
        self._stopped = True
        self.t_stop = time.monotonic() - self.t0
        for call in (lambda: self.session.cdp.send("Page.stopScreencast"),
                     lambda: self.session.cdp.remove_listener("Page.screencastFrame", self._handler)):
            try:
                call()
            except Exception:
                log.debug("ignored error", exc_info=True)
        self._write_index()

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
        expected = f"{self.viewport['width']}x{self.viewport['height']}"
        odd = {k: n for k, n in self.frame_sizes.items() if k != expected}
        if odd:
            detail = ", ".join(f"{n} frame(s) {k}" for k, n in sorted(odd.items()))
            msg = f"screencast frames differ from the {expected} viewport ({detail}); scaled/padded to the viewport"
            self.note = f"{self.note}; {msg}" if self.note else msg
        data = {
            "fps": self.fps,
            "viewport": self.viewport,
            "capture_start_epoch": self.capture_start_epoch,
            "t_stop": self.t_stop,
            "frame_count": len(self.frames),
            "dropped": self._dropped,
            "frame_sizes": dict(self.frame_sizes),
            "note": self.note,
            "frames": [{"file": f"frames/{name}", "t": round(t, 4)} for name, t in self.frames],
        }
        try:
            self.index_path.write_text(json.dumps(data, indent=1), encoding="utf-8")
        except OSError:
            pass

    def kept_frames(self) -> list[tuple[str, float]]:
        """Frames that go into the concat list, anchored to their real timestamps.

        The concat demuxer places entry i at the *sum* of the previous durations,
        so a minimum per-frame duration would push every later frame behind its
        real time (bursts of mouse-move/ack frames a few ms apart added ~0.25 s
        over an 8 s capture, and the narration, placed by the real clock, then led
        the picture).  Instead, a frame that arrives less than half a frame
        period after the previously kept one is dropped (its picture is
        indistinguishable at 30 fps) and every kept frame lasts exactly until
        the next kept one, so cumulative time equals real time.
        """
        min_gap = 1.0 / self.fps / 2
        kept: list[tuple[str, float]] = []
        for name, t in self.frames:
            if kept and t - kept[-1][1] < min_gap:
                continue
            kept.append((name, t))
        return kept

    def concat_list(self) -> str:
        """The ffconcat list: every kept frame is shown until the next one; the last frame is held to the stop time."""
        total = max(self.t_stop or 0.0, 0.0)
        lines = ["ffconcat version 1.0"]
        kept = self.kept_frames()
        n = len(kept)
        for i, (name, t) in enumerate(kept):
            start = 0.0 if i == 0 else t
            if i + 1 < n:
                duration = kept[i + 1][1] - start
            else:
                duration = max(total - start, 1.0 / self.fps)   # hold the last picture to t_stop
            lines.append(f"file 'frames/{name}'")
            lines.append(f"duration {duration:.6f}")
        # ffmpeg only honours the duration of the last entry when it is followed by another entry.
        last_name = kept[-1][0]
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
              screen_index: int = 0, display: str | None = None, scale: float = 1.0) -> list[str]:
    """ffmpeg argv for the OS screen grabber (pure; ``os_name`` is Windows/Darwin/Linux).

    ``bounds`` is the rectangle to record in DIPs (Chrome window coordinates);
    ``scale`` is the display's backing scale factor.  gdigrab, avfoundation and
    x11grab all address physical pixels, so the rectangle is multiplied by
    ``scale`` for the grab and the frames are scaled back to ``bounds`` size so
    the capture matches the viewport (and the edit timeline) 1:1.
    ``screen_index`` is the macOS display number (0 = main display, where the
    window is placed); the device is addressed by name ("Capture screen N") so
    the number of cameras attached does not shift it.
    """
    scale = float(scale) if scale and scale > 0 else 1.0
    w, h = _even(bounds.get("width", 1280)), _even(bounds.get("height", 720))
    x, y = round(int(bounds.get("x", 0)) * scale), round(int(bounds.get("y", 0)) * scale)
    gw, gh = _even(round(w * scale)), _even(round(h * scale))
    vf = [] if scale == 1.0 else ["-vf", f"scale={w}:{h}:flags=bicubic"]
    encode = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p",
              "-movflags", "+faststart", str(out_path)]
    head = [ffmpeg, "-hide_banner", "-loglevel", "warning", "-y"]
    if os_name == "Windows":
        return head + [
            "-f", "gdigrab", "-framerate", str(fps), "-draw_mouse", "1",
            "-offset_x", str(x), "-offset_y", str(y), "-video_size", f"{gw}x{gh}",
            "-i", "desktop",
        ] + vf + encode
    if os_name == "Darwin":
        crop = f"crop={gw}:{gh}:{x}:{y}"
        return head + [
            "-f", "avfoundation", "-framerate", str(fps), "-capture_cursor", "1",
            "-i", f"Capture screen {int(screen_index)}:none",
            "-vf", crop if scale == 1.0 else f"{crop},scale={w}:{h}:flags=bicubic",
        ] + encode
    disp = display or os.environ.get("DISPLAY") or ":0"
    if "." not in disp.split(":")[-1]:
        disp = disp + ".0"
    return head + [
        "-f", "x11grab", "-framerate", str(fps), "-draw_mouse", "1",
        "-video_size", f"{gw}x{gh}", "-i", f"{disp}+{x},{y}",
    ] + vf + encode


def page_bounds(session) -> dict:
    """The page area to record, in DIPs: window bounds offset by the browser-UI
    insets and sized to the viewport.  Falls back to the whole window (or the
    viewport at 0,0) when the session does not expose insets."""
    viewport = dict(getattr(session, "viewport", None) or {"width": 1280, "height": 720})
    window = dict(getattr(session, "window_bounds", None) or {"x": 0, "y": 0, **viewport})
    insets = getattr(session, "ui_insets", None)
    if not isinstance(insets, dict):
        return window
    return {
        "x": int(window.get("x", 0)) + int(insets.get("x", 0)),
        "y": int(window.get("y", 0)) + int(insets.get("y", 0)),
        "width": int(viewport["width"]),
        "height": int(viewport["height"]),
    }


class ScreenCapture:
    """OS screen grabber over the Chrome window bounds (needs a display)."""

    def __init__(self, session, out: Path, fps: int = 30):
        self.session = session
        self.out = Path(out)
        self.fps = int(fps)
        self.paths = _paths(self.out)
        self.path = self.paths.raw / "capture.mp4"
        self.log_path = self.paths.logs / "screen-capture.log"
        self.bounds = page_bounds(session)
        self.scale = float(getattr(session, "device_scale_factor", 1.0) or 1.0)
        self.screen_index = int(os.environ.get("DEMO_SMOKE_SCREEN_INDEX", "0") or 0)
        self.note = ""
        self.t0: float | None = None
        self.t_stop: float | None = None
        self.capture_start_epoch: float | None = None
        self._proc: subprocess.Popen | None = None
        self._log = None
        self._stopped = False

    def args(self, ffmpeg: str | None = None, os_name: str | None = None) -> list[str]:
        return grab_args(ffmpeg or find_ffmpeg(), os_name or platform.system(), self.bounds, self.fps, self.path,
                         screen_index=self.screen_index, scale=self.scale)

    def start(self) -> float:
        if self._proc is not None:
            return self.t0
        if getattr(self.session, "headless", False):
            raise CaptureError("screen capture needs a visible window: use --capture screencast with --headless")
        if platform.system() == "Linux" and os.environ.get("WAYLAND_DISPLAY"):
            # x11grab only sees X11 (XWayland) surfaces; a native Wayland Chrome window records black.
            self.note = ("Wayland session detected: x11grab cannot see native Wayland windows; "
                         "use --capture screencast or run Chrome under XWayland")
            log.warning(self.note)
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
        if self.t0 is not None:
            self.t_stop = time.monotonic() - self.t0
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

    def abort(self) -> None:
        """Stop the grabber without producing a usable file (error path); never raises."""
        if self._stopped:
            return
        self._stopped = True
        if self.t0 is not None:
            self.t_stop = time.monotonic() - self.t0
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                if proc.stdin and not proc.stdin.closed:
                    proc.stdin.write(b"q\n")
                    proc.stdin.flush()
                    proc.stdin.close()
                proc.wait(timeout=3)
            except Exception:
                log.debug("ignored error", exc_info=True)
                try:
                    proc.kill()
                    proc.wait(timeout=3)
                except Exception:
                    log.debug("ignored error", exc_info=True)
        if self._log and not self._log.closed:
            try:
                self._log.close()
            except Exception:
                log.debug("ignored error", exc_info=True)

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
