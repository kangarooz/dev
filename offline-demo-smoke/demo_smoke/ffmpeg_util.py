"""Small ffmpeg helper used by ``edit`` and ``verify``.

* locate the binary: ``DEMO_SMOKE_FFMPEG`` -> ``ffmpeg`` on PATH -> ``imageio_ffmpeg``
* run it with ``subprocess`` (argument lists, never a shell) and capture stderr
* parse the ``ffmpeg -i`` banner (imageio-ffmpeg ships no ffprobe)
* run analysis filters (``blackdetect``, ``volumedetect``) and parse their stderr

Every expected failure is raised as :class:`FfmpegError` with a one-line message.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
from pathlib import Path


class FfmpegError(RuntimeError):
    """ffmpeg missing or an ffmpeg run failed (message is a single line)."""


# --------------------------------------------------------------------------- discovery


def find_ffmpeg() -> str:
    """``DEMO_SMOKE_FFMPEG`` -> ``ffmpeg`` on PATH -> ``imageio_ffmpeg.get_ffmpeg_exe()``."""
    env = os.environ.get("DEMO_SMOKE_FFMPEG")
    if env:
        if Path(env).is_file():
            return str(Path(env))
        raise FfmpegError(f"DEMO_SMOKE_FFMPEG points to a missing file: {env}")
    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path
    with contextlib.suppress(ImportError, RuntimeError, OSError):
        import imageio_ffmpeg  # type: ignore

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).is_file():
            return str(exe)
    raise FfmpegError(
        "ffmpeg not found: install ffmpeg, or `pip install imageio-ffmpeg`, "
        "or set DEMO_SMOKE_FFMPEG=/path/to/ffmpeg"
    )


# --------------------------------------------------------------------------- running


def _tail(text: str, lines: int = 3) -> str:
    keep = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return " | ".join(keep[-lines:]) if keep else "(no output)"


def run(args: list[str], timeout: int = 900, check: bool = True,
        what: str = "ffmpeg") -> subprocess.CompletedProcess:
    """Run ``ffmpeg -hide_banner -nostdin <args>`` and return the CompletedProcess.

    ``args`` must not include the executable.  With ``check`` a non-zero exit
    becomes ``FfmpegError`` carrying the last few stderr lines.
    """
    exe = find_ffmpeg()
    cmd = [exe, "-hide_banner", "-nostdin", *[str(a) for a in args]]
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                            timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise FfmpegError(f"{what} timed out after {timeout} s") from None
    except OSError as e:
        raise FfmpegError(f"{what} could not start ({exe}): {e}") from None
    if check and cp.returncode != 0:
        raise FfmpegError(f"{what} failed (exit {cp.returncode}): {_tail(cp.stderr)}")
    return cp


def argv(args: list[str]) -> list[str]:
    """The exact command line :func:`run` would execute (for logs)."""
    return [find_ffmpeg(), "-hide_banner", "-nostdin", *[str(a) for a in args]]


# --------------------------------------------------------------------------- ffmpeg -i parsing

_DUR_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_STREAM_RE = re.compile(r"Stream #\d+:\d+.*?:\s*(Video|Audio):\s*(.*)")
_RES_RE = re.compile(r"^(\d{2,5})x(\d{2,5})$")
_FPS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*fps")
_HZ_RE = re.compile(r"(\d+)\s*Hz")
_TIME_RE = re.compile(r"time=\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def _hms(h: str, m: str, s: str) -> float:
    return int(h) * 3600 + int(m) * 60 + float(s)


def parse_info(stderr: str) -> dict:
    """Parse the banner ``ffmpeg -i FILE`` prints on stderr.

    Returns ``{"duration","width","height","fps","video_codec","has_audio",
    "audio_codec","sample_rate","audio_duration"}``; missing pieces are 0/None/False.
    """
    info: dict = {"duration": 0.0, "width": 0, "height": 0, "fps": None, "video_codec": None,
                  "has_audio": False, "audio_codec": None, "sample_rate": None,
                  "audio_duration": None}
    m = _DUR_RE.search(stderr or "")
    if m:
        info["duration"] = _hms(*m.groups())
    for line in (stderr or "").splitlines():
        sm = _STREAM_RE.search(line)
        if not sm:
            continue
        kind, rest = sm.group(1), sm.group(2)
        fields = [f.strip() for f in rest.split(",")]
        if kind == "Video" and not info["width"]:
            info["video_codec"] = fields[0].split(" ")[0] if fields else None
            for tok in fields:
                rm = _RES_RE.match(tok.split(" ")[0])
                if rm:
                    info["width"], info["height"] = int(rm.group(1)), int(rm.group(2))
                    break
            fm = _FPS_RE.search(rest)
            if fm:
                info["fps"] = float(fm.group(1))
        elif kind == "Audio" and not info["has_audio"]:
            info["has_audio"] = True
            info["audio_codec"] = fields[0].split(" ")[0] if fields else None
            hm = _HZ_RE.search(rest)
            if hm:
                info["sample_rate"] = int(hm.group(1))
    if info["has_audio"]:
        info["audio_duration"] = info["duration"]
    return info


def media_info(path: str | Path) -> dict:
    """Container-level info for ``path`` via ``ffmpeg -i`` (see :func:`parse_info`)."""
    p = Path(path)
    if not p.is_file():
        raise FfmpegError(f"media file not found: {p}")
    cp = run(["-i", str(p)], check=False, what=f"ffmpeg -i {p.name}")
    info = parse_info(cp.stderr)
    if not info["width"] and not info["has_audio"]:
        raise FfmpegError(f"{p.name} is not a readable media file: {_tail(cp.stderr, 1)}")
    return info


def parse_last_time(stderr: str) -> float | None:
    """The last ``time=HH:MM:SS.ss`` progress value in an ffmpeg stderr log."""
    times = _TIME_RE.findall(stderr or "")
    return _hms(*times[-1]) if times else None


def stream_duration(path: str | Path, kind: str) -> float | None:
    """Decoded length of the ``"video"`` or ``"audio"`` stream (seconds), by
    decoding it to the null muxer and reading the final progress time."""
    flag = "-an" if kind == "video" else "-vn"
    cp = run(["-v", "info", "-stats", "-i", str(path), flag, "-f", "null", "-"],
             check=False, what=f"decode {kind} of {Path(path).name}")
    if cp.returncode != 0:
        return None
    return parse_last_time(cp.stderr)


# --------------------------------------------------------------------------- analysis filters

_BLACK_RE = re.compile(r"black_start:\s*(\d+(?:\.\d+)?)\s+black_end:\s*(\d+(?:\.\d+)?)"
                       r"\s+black_duration:\s*(\d+(?:\.\d+)?)")
_MEAN_RE = re.compile(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")
_MAX_RE = re.compile(r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")


def parse_blackdetect(stderr: str) -> list[dict]:
    """``[{"start","end","duration"}]`` from blackdetect stderr lines."""
    return [{"start": float(a), "end": float(b), "duration": float(c)}
            for a, b, c in _BLACK_RE.findall(stderr or "")]


def parse_volumedetect(stderr: str) -> dict:
    """``{"mean_volume": float|None, "max_volume": float|None}`` (dB) from volumedetect stderr.

    volumedetect may print a second, empty instance (``n_samples: 0``); the
    values are taken from whichever instance reported them.
    """
    mean = _MEAN_RE.findall(stderr or "")
    peak = _MAX_RE.findall(stderr or "")
    return {"mean_volume": float(mean[-1]) if mean else None,
            "max_volume": float(peak[-1]) if peak else None}


def _window(ss: float | None, t: float | None) -> list[str]:
    args: list[str] = []
    if ss is not None and ss > 0:
        args += ["-ss", f"{max(0.0, float(ss)):.3f}"]
    if t is not None:
        args += ["-t", f"{max(0.0, float(t)):.3f}"]
    return args


def blackdetect(path: str | Path, ss: float | None = None, t: float | None = None,
                d: float = 0.1, pic_th: float = 0.98) -> list[dict]:
    """Black intervals (times relative to ``ss``) in ``path`` over ``[ss, ss+t]``."""
    cp = run([*_window(ss, t), "-i", str(path), "-vf", f"blackdetect=d={d}:pic_th={pic_th}",
              "-an", "-f", "null", "-"], what=f"blackdetect on {Path(path).name}")
    return parse_blackdetect(cp.stderr)


def volumedetect(path: str | Path, ss: float | None = None, t: float | None = None) -> dict:
    """``{"mean_volume","max_volume"}`` in dB for the audio of ``path`` (None if no audio)."""
    cp = run([*_window(ss, t), "-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-"],
             check=False, what=f"volumedetect on {Path(path).name}")
    if cp.returncode != 0:
        return {"mean_volume": None, "max_volume": None}
    return parse_volumedetect(cp.stderr)


def thumbnail(path: str | Path, at: float, dst: str | Path) -> Path:
    """Write one PNG frame of ``path`` taken at ``at`` seconds to ``dst``."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    run(["-y", "-ss", f"{max(0.0, float(at)):.3f}", "-i", str(path), "-frames:v", "1",
         "-an", str(dst)], what=f"thumbnail at {at:.1f}s")
    if not dst.is_file():
        raise FfmpegError(f"thumbnail was not written: {dst}")
    return dst
