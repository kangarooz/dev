"""Environment discovery: OS, ffmpeg/ffprobe, Chrome, torch device, media info.

Nothing in here may raise for a *missing* component; ``detect`` reports
``None``/``False`` plus a hint instead.  Only ``find_ffmpeg`` raises, because
the pipeline cannot run without ffmpeg.
"""

from __future__ import annotations

import contextlib
import glob
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

_CHROME_CANDIDATES = {
    "Windows": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Chromium\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ],
    "Darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ],
    "Linux": [
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/snap/bin/chromium",
        "/opt/google/chrome/chrome",
    ],
}
_CHROME_PATH_NAMES = ["google-chrome", "chrome", "chromium", "chromium-browser"]


class Paths:
    """Output directory layout: ``raw/ audio/ clips/ final/ logs/`` (created on init)."""

    def __init__(self, out: str | Path):
        self.out = Path(out)
        self.raw = self.out / "raw"
        self.audio = self.out / "audio"
        self.clips = self.out / "clips"
        self.final = self.out / "final"
        self.logs = self.out / "logs"
        for d in (self.out, self.raw, self.audio, self.clips, self.final, self.logs):
            d.mkdir(parents=True, exist_ok=True)

    def __fspath__(self) -> str:
        return str(self.out)

    def __repr__(self) -> str:
        return f"Paths({str(self.out)!r})"


# --------------------------------------------------------------------------- ffmpeg


def find_ffmpeg() -> str:
    """DEMO_SMOKE_FFMPEG -> ffmpeg on PATH -> imageio_ffmpeg bundled binary."""
    env = os.environ.get("DEMO_SMOKE_FFMPEG")
    if env:
        if Path(env).is_file():
            return str(Path(env))
        raise RuntimeError(
            f"DEMO_SMOKE_FFMPEG points to a missing file: {env}"
        )
    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path
    with contextlib.suppress(ImportError, RuntimeError, OSError):
        import imageio_ffmpeg  # type: ignore

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).is_file():
            return str(exe)
    raise RuntimeError(
        "ffmpeg not found. Install ffmpeg (https://ffmpeg.org/download.html), "
        "or `pip install imageio-ffmpeg`, or set DEMO_SMOKE_FFMPEG=/path/to/ffmpeg"
    )


def find_ffprobe() -> str | None:
    env = os.environ.get("DEMO_SMOKE_FFPROBE")
    if env:
        return str(Path(env)) if Path(env).is_file() else None
    on_path = shutil.which("ffprobe")
    if on_path:
        return on_path
    # ffprobe often sits next to a real ffmpeg install (not next to imageio's).
    try:
        ff = Path(find_ffmpeg())
        sibling = ff.with_name("ffprobe" + ff.suffix if ff.suffix else "ffprobe")
        if sibling.is_file() and "imageio_ffmpeg" not in str(sibling):
            return str(sibling)
    except RuntimeError:
        pass
    return None


def ffmpeg_version(ffmpeg: str | None = None) -> str | None:
    try:
        exe = ffmpeg or find_ffmpeg()
        cp = subprocess.run([exe, "-version"], capture_output=True, text=True,
                            timeout=20, check=False)
        first = (cp.stdout or cp.stderr).splitlines()[0] if (cp.stdout or cp.stderr) else ""
        m = re.search(r"ffmpeg version (\S+)", first)
        return m.group(1) if m else (first.strip() or None)
    except (OSError, subprocess.SubprocessError, RuntimeError):
        return None


# --------------------------------------------------------------------------- chrome


def find_chrome() -> str | None:
    """DEMO_SMOKE_CHROME -> per-OS install paths -> PATH names -> Playwright cache."""
    env = os.environ.get("DEMO_SMOKE_CHROME")
    if env:
        return str(Path(env)) if Path(env).is_file() else None
    for cand in _CHROME_CANDIDATES.get(platform.system(), []):
        p = Path(os.path.expandvars(os.path.expanduser(cand)))
        if p.is_file():
            return str(p)
    for name in _CHROME_PATH_NAMES:
        found = shutil.which(name)
        if found:
            return found
    patterns = [
        "/opt/pw-browsers/chromium-*/chrome-linux/chrome",
        str(Path.home() / ".cache" / "ms-playwright" / "chromium-*" / "chrome-linux" / "chrome"),
        str(Path.home() / "Library" / "Caches" / "ms-playwright" / "chromium-*"
            / "chrome-mac" / "Chromium.app" / "Contents" / "MacOS" / "Chromium"),
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright" / "chromium-*"
            / "chrome-win" / "chrome.exe"),
    ]
    for pat in patterns:
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    return None


# --------------------------------------------------------------------------- torch


def torch_device() -> str:
    """'cuda' | 'rocm' | 'mps' | 'cpu' | 'none' (torch not importable)."""
    if importlib.util.find_spec("torch") is None:
        return "none"
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            return "rocm" if getattr(torch.version, "hip", None) else "cuda"
        mps = getattr(getattr(torch, "backends", None), "mps", None)
        if mps is not None and mps.is_available():
            return "mps"
        return "cpu"
    except Exception:  # noqa: BLE001 - a broken torch install must not crash doctor
        return "none"


def chatterbox_importable() -> bool:
    try:
        return importlib.util.find_spec("chatterbox") is not None
    except (ImportError, ValueError, AttributeError):
        return False


def hf_cache_dir() -> str:
    home = os.environ.get("HF_HOME")
    if home:
        return str(Path(home) / "hub")
    return str(Path.home() / ".cache" / "huggingface" / "hub")


# --------------------------------------------------------------------------- doctor


def detect(base_url: str | None = None, model: str | None = None) -> dict:
    """Doctor report.  Never raises: missing components are None/False + a hint."""
    hints: list[str] = []
    rep: dict = {
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "python": f"{platform.python_version()} ({sys.executable})",
        "ffmpeg": None,
        "ffmpeg_version": None,
        "ffprobe": None,
        "chrome": None,
        "torch_device": "none",
        "chatterbox": False,
        "hf_cache": hf_cache_dir(),
        "offline_env": {
            "HF_HUB_OFFLINE": os.environ.get("HF_HUB_OFFLINE"),
            "TRANSFORMERS_OFFLINE": os.environ.get("TRANSFORMERS_OFFLINE"),
        },
        "llm": None,
        "hints": hints,
    }
    try:
        rep["ffmpeg"] = find_ffmpeg()
        rep["ffmpeg_version"] = ffmpeg_version(rep["ffmpeg"])
    except (RuntimeError, OSError) as e:
        hints.append(str(e))
    try:
        rep["ffprobe"] = find_ffprobe()
    except (RuntimeError, OSError):
        rep["ffprobe"] = None
    if not rep["ffprobe"]:
        hints.append("ffprobe not found: media info falls back to parsing `ffmpeg -i` (fine)")
    try:
        rep["chrome"] = find_chrome()
    except OSError:
        rep["chrome"] = None
    if not rep["chrome"]:
        hints.append(
            "Chrome/Chromium not found: install Google Chrome or run "
            "`playwright install chromium`, or set DEMO_SMOKE_CHROME=/path/to/chrome"
        )
    rep["torch_device"] = torch_device()
    rep["chatterbox"] = chatterbox_importable()
    if rep["torch_device"] == "none" or not rep["chatterbox"]:
        hints.append(
            "Voice cloning unavailable (torch/chatterbox missing): "
            "`pip install -r requirements-tts.txt`; `--tts tone` works without them"
        )
    if base_url:
        llm: dict = {"base_url": base_url, "model": model, "reachable": False, "tool_call": None}
        try:
            from . import llm as _llm

            llm["reachable"] = _llm.reachable(base_url)
            if not llm["reachable"]:
                hints.append(f"LLM endpoint not reachable at {base_url} (is ollama/llama.cpp running?)")
            elif model:
                llm["tool_call"] = _llm.probe_tool_call(base_url, model)
                if not llm["tool_call"]["pass"]:
                    hints.append(
                        f"model {model} did not return a tool call; pick a tool-capable model "
                        "(see README model table)"
                    )
        except Exception as e:  # noqa: BLE001 - doctor reports, never crashes
            llm["error"] = str(e)
            hints.append(f"LLM probe failed: {e}")
        rep["llm"] = llm
    return rep


# --------------------------------------------------------------------------- media info

_DUR_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VIDEO_RE = re.compile(r"Stream #\d+:\d+.*?:\s*Video:\s*(.*)")
_AUDIO_RE = re.compile(r"Stream #\d+:\d+.*?:\s*Audio:\s*(.*)")
_TIME_RE = re.compile(r"time=\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_RES_RE = re.compile(r"^(\d{2,5})x(\d{2,5})$")


def _hms(h: str, m: str, s: str) -> float:
    return int(h) * 3600 + int(m) * 60 + float(s)


def _run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          errors="replace", check=False)


def _info_ffprobe(ffprobe: str, path: Path) -> dict | None:
    cp = _run([ffprobe, "-v", "error", "-print_format", "json", "-show_format",
               "-show_streams", str(path)])
    if cp.returncode != 0 or not cp.stdout.strip():
        return None
    try:
        data = json.loads(cp.stdout)
    except json.JSONDecodeError:
        return None
    fmt = data.get("format") or {}
    duration = float(fmt.get("duration") or 0.0)
    width = height = 0
    has_audio = False
    audio_duration = None
    for st in data.get("streams") or []:
        if st.get("codec_type") == "video" and not width:
            width = int(st.get("width") or 0)
            height = int(st.get("height") or 0)
            if not duration and st.get("duration"):
                duration = float(st["duration"])
        elif st.get("codec_type") == "audio" and not has_audio:
            has_audio = True
            if st.get("duration"):
                audio_duration = float(st["duration"])
            elif st.get("tags", {}).get("DURATION"):
                m = re.match(r"(\d+):(\d+):(\d+(?:\.\d+)?)", st["tags"]["DURATION"])
                if m:
                    audio_duration = _hms(*m.groups())
    if has_audio and audio_duration is None:
        audio_duration = duration
    return {"duration": duration, "width": width, "height": height,
            "has_audio": has_audio, "audio_duration": audio_duration}


def parse_ffmpeg_i(stderr: str) -> dict:
    """Parse the banner ``ffmpeg -i FILE`` prints on stderr (no ffprobe needed)."""
    duration = 0.0
    m = _DUR_RE.search(stderr)
    if m:
        duration = _hms(*m.groups())
    width = height = 0
    has_audio = False
    for line in stderr.splitlines():
        vm = _VIDEO_RE.search(line)
        if vm and not width:
            for tok in vm.group(1).split(","):
                tok = tok.strip().split(" ")[0]
                rm = _RES_RE.match(tok)
                if rm:
                    width, height = int(rm.group(1)), int(rm.group(2))
                    break
        elif _AUDIO_RE.search(line):
            has_audio = True
    return {"duration": duration, "width": width, "height": height,
            "has_audio": has_audio, "audio_duration": duration if has_audio else None}


def _decoded_audio_seconds(ffmpeg: str, path: Path) -> float | None:
    """Decode only the audio to /dev/null and read the final ``time=`` progress."""
    cp = _run([ffmpeg, "-hide_banner", "-nostdin", "-v", "info", "-i", str(path),
               "-vn", "-f", "null", "-"], timeout=600)
    times = _TIME_RE.findall(cp.stderr or "")
    if not times:
        return None
    return _hms(*times[-1])


def media_info(path: str | Path, measure_audio: bool = True) -> dict:
    """{"duration","width","height","has_audio","audio_duration"} for a media file.

    Prefers ffprobe JSON; falls back to parsing ``ffmpeg -i`` (plus an audio-only
    decode for an accurate ``audio_duration`` when ``measure_audio`` is true).
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"media file not found: {p}")
    ffprobe = find_ffprobe()
    if ffprobe:
        info = _info_ffprobe(ffprobe, p)
        if info is not None:
            return info
    ffmpeg = find_ffmpeg()
    cp = _run([ffmpeg, "-hide_banner", "-nostdin", "-i", str(p)])
    info = parse_ffmpeg_i(cp.stderr or "")
    if info["has_audio"] and measure_audio and info["width"]:
        measured = _decoded_audio_seconds(ffmpeg, p)
        if measured is not None:
            info["audio_duration"] = measured
    return info
