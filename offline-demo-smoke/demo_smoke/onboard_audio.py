"""Onboarding audio: ``record-ref`` (record a reference voice) and ``devices``.

``record-ref`` resolves the recording backend first (sounddevice/PortAudio, or
ffmpeg's OS audio grabber: dshow / avfoundation / pulse / alsa) and opens the
input once so driver start-up and the macOS microphone prompt happen before
anyone starts reading; then it prints the reading passage (``passage.txt``) in
three chunks, counts down 3-2-1, records mono 48 kHz for ``--seconds``, peak-
normalises to -3 dBFS, trims leading and trailing silence at -40 dBFS (200 ms
padding), writes a PCM16 WAV plus a ``<name>.json`` sidecar with stats and
warnings.  Exit 4 when a warning fires (the file is still saved), 3 when nothing
could record, 130 on Ctrl-C.

``devices`` lists sounddevice input devices and the screens ``--capture screen``
can grab; every probe degrades to an "unavailable" note instead of raising.

Everything here is importable without sounddevice: it is imported lazily so
tests inject a fake module into ``sys.modules``.  ``register(subparsers, run_map)``
wires both commands into the ``python -m demo_smoke`` parser.

Contract note: ``record-ref --out`` is a *file* path (``voices/<name>.wav``),
not a directory, so its JSON goes to ``voices/<name>.json`` rather than
``<out>/logs/record-ref.json``; the handlers never raise, so the generic
``cli.main`` failure logger (which treats ``--out`` as a directory) is never hit.
"""

from __future__ import annotations

import importlib
import json
import math
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

EXIT_OK = 0
EXIT_ERROR = 3
EXIT_BAD_INPUT = 4
EXIT_INTERRUPTED = 130

SAMPLE_RATE = 48000
DEFAULT_SECONDS = 60.0
TRIM_DB = -40.0            # silence threshold for leading/trailing trim
TRIM_PAD_S = 0.2           # keep this much around the first/last speech frame
TARGET_PEAK_DB = -3.0      # peak normalisation target
FRAME_MS = 50              # analysis frame for noise floor / speech detection
TRIM_FRAME_MS = 20         # finer frame for trim boundaries
MIN_SPEECH_S = 20.0
MIN_SNR_DB = 15.0
CLIP_LEVEL = 0.999
CLIP_PCT_LIMIT = 0.5       # % of samples at full scale that counts as clipped
NOISE_PERCENTILE = 10
SPEECH_ABOVE_FLOOR_DB = 6.0
DB_FLOOR = -120.0

SILENT_PEAK_DB = -60.0     # raw peak below this: no signal reached the ADC at all

WARN_SILENT = "silent (no signal)"
WARN_SHORT = "too short (<20 s of speech)"
WARN_NOISY = "noisy (SNR < 15 dB)"
WARN_CLIPPED = "clipped"

PASSAGE_PATH = Path(__file__).with_name("passage.txt")
BACKENDS = ("auto", "sounddevice", "ffmpeg")


class RecordError(RuntimeError):
    """No backend could record (exit 3)."""


# --------------------------------------------------------------------------- small helpers


def _say(msg: str = "") -> None:
    print(msg, flush=True)


def _err(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr, flush=True)


def _db(x: float) -> float:
    return 20.0 * math.log10(x) if x > 0 else DB_FLOOR


def _round(x: float, n: int = 2) -> float:
    return round(float(x), n) if math.isfinite(x) else DB_FLOOR


def _one_line(exc: BaseException) -> str:
    text = " ".join(str(exc).split()) or exc.__class__.__name__
    return text[:200]


def _sleep(seconds: float) -> None:   # monkeypatched in tests
    time.sleep(seconds)


def _find_ffmpeg() -> str | None:
    try:
        from .env import find_ffmpeg

        return find_ffmpeg()
    except Exception:  # noqa: BLE001 - listing must not crash without ffmpeg
        return None


def _run_capture(argv: list[str], timeout: float = 20) -> str:
    """Run a listing command and return stdout+stderr (ffmpeg lists devices on stderr).

    ffmpeg prints device names as UTF-8 on every OS (dshow converts the wide names
    itself), so decode them as UTF-8 rather than with the console code page: with
    cp1252 an Intel ``Microphone Array (Intel® ...)`` becomes ``IntelÂ®`` and the
    name can no longer be opened."""
    cp = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                        timeout=timeout, check=False)
    return (cp.stderr or "") + "\n" + (cp.stdout or "")


def _run_record(argv: list[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, check=False)


# --------------------------------------------------------------------------- passage


def passage_text() -> str:
    return PASSAGE_PATH.read_text(encoding="utf-8").strip()


def passage_chunks(text: str | None = None, n: int = 3) -> list[str]:
    """Split the passage into ``n`` roughly equal paragraphs on sentence boundaries."""
    text = " ".join((text if text is not None else passage_text()).split())
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text) if s]
    if not sentences:
        return []
    total = sum(len(s.split()) for s in sentences)
    chunks: list[list[str]] = [[] for _ in range(max(1, n))]
    i, count = 0, 0
    for s in sentences:
        words = len(s.split())
        # start the next part when adding this sentence would overshoot the boundary
        # by more than stopping here undershoots it (never leave a part empty)
        if i < n - 1 and chunks[i]:
            target = total * (i + 1) / n
            if abs(count + words - target) > abs(count - target):
                i += 1
        chunks[i].append(s)
        count += words
    return [" ".join(c) for c in chunks if c]


def print_passage(chunks: list[str] | None = None) -> None:
    chunks = chunks if chunks is not None else passage_chunks()
    _say("Read this aloud at a relaxed pace (it is fine to pause between parts):")
    for i, c in enumerate(chunks, 1):
        _say("")
        _say(f"--- part {i}/{len(chunks)} ---")
        _say(c)
    _say("")


# --------------------------------------------------------------------------- dsp


def to_mono(a) -> np.ndarray:
    x = np.asarray(a)
    if x.dtype.kind in "iu":
        x = x.astype(np.float64) / float(np.iinfo(x.dtype).max)
    x = x.astype(np.float64)
    if x.ndim == 2:
        x = x.mean(axis=1) if x.shape[1] > 1 else x[:, 0]
    return x.reshape(-1)


def frame_rms_db(a: np.ndarray, sr: int, frame_ms: int = FRAME_MS) -> np.ndarray:
    """RMS in dBFS of consecutive non-overlapping ``frame_ms`` frames (last partial frame kept)."""
    x = np.asarray(a, dtype=np.float64).reshape(-1)
    size = max(1, int(sr * frame_ms / 1000))
    if x.size == 0:
        return np.zeros(0)
    n = math.ceil(x.size / size)
    padded = np.zeros(n * size)
    padded[: x.size] = x
    rms = np.sqrt(np.mean(padded.reshape(n, size) ** 2, axis=1))
    # the last (partial) frame is zero-padded; scale it back to the real sample count
    if x.size % size:
        rms[-1] *= math.sqrt(size / (x.size % size))
    with np.errstate(divide="ignore"):
        db = 20.0 * np.log10(rms)
    return np.where(np.isfinite(db), db, DB_FLOOR)


def normalize_peak(a: np.ndarray, target_db: float = TARGET_PEAK_DB) -> tuple[np.ndarray, float]:
    """Scale so the peak sits at ``target_db`` dBFS; returns (audio, gain_db).  Silence is left alone."""
    x = np.asarray(a, dtype=np.float64).reshape(-1)
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    if peak <= 0:
        return x, 0.0
    gain = 10 ** (target_db / 20.0) / peak
    return x * gain, _round(20.0 * math.log10(gain))


def trim_silence(a: np.ndarray, sr: int, threshold_db: float = TRIM_DB, pad_s: float = TRIM_PAD_S,
                 frame_ms: int = TRIM_FRAME_MS) -> tuple[np.ndarray, int, int]:
    """Drop leading/trailing frames below ``threshold_db`` keeping ``pad_s`` around the speech.

    Returns (audio, start_sample, end_sample).  When nothing is above the
    threshold the input is returned untouched (there is nothing to keep)."""
    x = np.asarray(a, dtype=np.float64).reshape(-1)
    db = frame_rms_db(x, sr, frame_ms)
    active = np.flatnonzero(db > threshold_db)
    if active.size == 0:
        return x, 0, int(x.size)
    size = max(1, int(sr * frame_ms / 1000))
    pad = int(pad_s * sr)
    start = max(0, int(active[0]) * size - pad)
    end = min(int(x.size), (int(active[-1]) + 1) * size + pad)
    return x[start:end], start, end


def clipped_pct(a: np.ndarray, level: float = CLIP_LEVEL) -> float:
    x = np.asarray(a, dtype=np.float64).reshape(-1)
    if x.size == 0:
        return 0.0
    return _round(100.0 * float(np.mean(np.abs(x) >= level)), 3)


def analyze(a: np.ndarray, sr: int, clipped_percent: float = 0.0) -> dict:
    """Stats of the (normalised, trimmed) recording.

    noise floor = 10th percentile of 50 ms frame RMS; speech frames are those
    more than 6 dB above it; ``snr_db`` = speech RMS - noise floor."""
    x = np.asarray(a, dtype=np.float64).reshape(-1)
    duration = x.size / float(sr) if sr else 0.0
    if x.size == 0:
        return {"duration": 0.0, "peak_dbfs": DB_FLOOR, "rms_dbfs": DB_FLOOR,
                "noise_floor_dbfs": DB_FLOOR, "speech_rms_dbfs": DB_FLOOR, "speech_seconds": 0.0,
                "snr_db": 0.0, "clipped_pct": clipped_percent, "clipped": False}
    peak_db = _db(float(np.max(np.abs(x))))
    rms_db = _db(float(np.sqrt(np.mean(x ** 2))))
    frames = frame_rms_db(x, sr, FRAME_MS)
    noise_floor = float(np.percentile(frames, NOISE_PERCENTILE))
    speech = frames > (noise_floor + SPEECH_ABOVE_FLOOR_DB)
    frame_s = FRAME_MS / 1000.0
    if speech.any():
        lin = 10 ** (frames[speech] / 20.0)
        speech_rms = _db(float(np.sqrt(np.mean(lin ** 2))))
        speech_seconds = float(speech.sum()) * frame_s
        snr = speech_rms - noise_floor
    else:   # nothing stands out of the floor: report the (small) overall headroom, never negative
        speech_rms, speech_seconds = rms_db, 0.0
        snr = max(0.0, rms_db - noise_floor)
    return {
        "duration": round(duration, 3),
        "peak_dbfs": _round(peak_db),
        "rms_dbfs": _round(rms_db),
        "noise_floor_dbfs": _round(noise_floor),
        "speech_rms_dbfs": _round(speech_rms),
        "speech_seconds": round(speech_seconds, 2),
        "snr_db": _round(snr),
        "clipped_pct": clipped_percent,
        "clipped": bool(clipped_percent > CLIP_PCT_LIMIT),
    }


def warnings_for(stats: dict) -> list[str]:
    w = []
    if stats.get("raw_peak_dbfs", 0.0) < SILENT_PEAK_DB:
        w.append(WARN_SILENT)
    if stats.get("speech_seconds", 0.0) < MIN_SPEECH_S:
        w.append(WARN_SHORT)
    if stats.get("snr_db", 0.0) < MIN_SNR_DB:
        w.append(WARN_NOISY)
    if stats.get("clipped"):
        w.append(WARN_CLIPPED)
    return w


def process(raw, sr: int) -> tuple[np.ndarray, dict]:
    """Raw capture -> (float64 mono, stats).  Normalise, trim, analyse.

    Normalising before trimming gives the same output as the reverse order
    (the peak sample is always inside the kept region) but makes the -40 dBFS
    trim threshold independent of the microphone gain."""
    mono = to_mono(raw)
    clip = clipped_pct(mono)
    raw_duration = round(mono.size / float(sr), 3) if sr else 0.0
    raw_peak_db = _db(float(np.max(np.abs(mono)))) if mono.size else DB_FLOOR
    normed, gain_db = normalize_peak(mono)
    trimmed, start, end = trim_silence(normed, sr)
    stats = analyze(trimmed, sr, clip)
    stats.update({"raw_duration": raw_duration, "gain_db": gain_db, "raw_peak_dbfs": _round(raw_peak_db),
                  "trim": {"start_s": round(start / sr, 3), "end_s": round(end / sr, 3)}})
    stats["warnings"] = warnings_for(stats)
    return trimmed, stats


def write_wav16(path: Path, a: np.ndarray, sr: int) -> Path:
    import soundfile as sf

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    x = np.clip(np.asarray(a, dtype=np.float64).reshape(-1), -1.0, 1.0).astype(np.float32)
    sf.write(str(p), x, sr, subtype="PCM_16")
    return p


# --------------------------------------------------------------------------- device listing


SD_NOTE_PIP = "pip install sounddevice (it is in requirements.txt: re-run scripts/setup)"
SD_NOTE_PORTAUDIO = ("PortAudio library missing: apt install libportaudio2 (Debian/Ubuntu), "
                     "dnf install portaudio (Fedora) or brew install portaudio (macOS)")


def _import_sounddevice_detail() -> tuple:
    """``(module, None)`` or ``(None, note)`` where the note names the actual fix:
    ``ImportError`` = the package is absent, ``OSError`` = PortAudio's shared library is."""
    try:
        return importlib.import_module("sounddevice"), None
    except ImportError as e:
        return None, f"sounddevice not installed ({_one_line(e)}): {SD_NOTE_PIP}"
    except OSError as e:
        return None, f"sounddevice found but {SD_NOTE_PORTAUDIO} ({_one_line(e)})"
    except Exception as e:  # noqa: BLE001 - anything else PortAudio init can throw
        return None, f"sounddevice import failed ({_one_line(e)})"


def _import_sounddevice():
    """The sounddevice module or ``None`` (missing package or missing PortAudio library)."""
    return _import_sounddevice_detail()[0]


def _hostapi_names(sd) -> list[str]:
    try:
        return [str(a.get("name", "")) for a in sd.query_hostapis()]
    except Exception:  # noqa: BLE001 - optional decoration only
        return []


def _device_default_rate(sd, device: int | None) -> int | None:
    """The device's (or the default input's) ``default_samplerate``, or None."""
    try:
        idx = device if device is not None else _default_input_index(sd)
        if idx is None:
            return None
        rate = int(float(sd.query_devices(idx)["default_samplerate"]))
        return rate if rate > 0 else None
    except Exception:  # noqa: BLE001
        return None


def _default_input_index(sd) -> int | None:
    try:
        dev = sd.default.device
        idx = dev[0] if isinstance(dev, (list, tuple)) or hasattr(dev, "__getitem__") else dev
        idx = int(idx)
        return idx if idx >= 0 else None
    except Exception:  # noqa: BLE001
        return None


def list_input_devices() -> dict:
    """{"available": bool, "devices": [{"index","name","channels","default","samplerate"}], "note"}"""
    sd, note = _import_sounddevice_detail()
    if sd is None:
        return {"available": False, "devices": [], "note": f"{note}; record-ref falls back to ffmpeg"}
    try:
        raw = sd.query_devices()
        default = _default_input_index(sd)
        apis = _hostapi_names(sd)
        devices = []
        for i, d in enumerate(raw):
            info = dict(d) if isinstance(d, dict) else {"name": str(d)}
            ch = int(info.get("max_input_channels", 0) or 0)
            if ch <= 0:
                continue
            name = str(info.get("name", f"device {i}"))
            # Windows lists every microphone once per host API (MME names cut at 31 chars,
            # DirectSound, WASAPI, WDM-KS): label them so the entries can be told apart.
            if len(apis) > 1:
                try:
                    name += f" [{apis[int(info.get('hostapi'))]}]"
                except (TypeError, ValueError, IndexError):
                    pass
            devices.append({"index": i, "name": name, "channels": ch,
                            "default": i == default,
                            "samplerate": info.get("default_samplerate")})
        note = None if devices else "no input devices found (is a microphone connected?)"
        return {"available": True, "devices": devices, "note": note}
    except Exception as e:  # noqa: BLE001 - PortAudio errors must not crash the listing
        return {"available": False, "devices": [], "note": f"sounddevice query failed: {_one_line(e)}"}


_AVF_LINE = re.compile(r"\[(\d+)\]\s+(.+?)\s*$")


def parse_avfoundation_devices(text: str) -> dict:
    """Parse ``ffmpeg -f avfoundation -list_devices true -i ""`` -> {"video": [(idx, name)], "audio": [...]}"""
    out: dict = {"video": [], "audio": []}
    section = None
    for line in text.splitlines():
        body = re.sub(r"^\[AVFoundation[^\]]*\]\s*", "", line.strip())
        low = body.lower()
        if "video devices" in low:
            section = "video"
            continue
        if "audio devices" in low:
            section = "audio"
            continue
        m = _AVF_LINE.match(body)
        if m and section:
            out[section].append((int(m.group(1)), m.group(2).strip()))
    return out


_DSHOW_NAME = re.compile(r'^"(.+?)"(?:\s*\((video|audio)\))?\s*$')


def parse_dshow_devices(text: str) -> dict:
    """Parse ``ffmpeg -list_devices true -f dshow -i dummy`` (old and new layouts) -> {"video": [names], "audio": [names]}"""
    out: dict = {"video": [], "audio": []}
    section = None
    for line in text.splitlines():
        body = re.sub(r"^\[dshow[^\]]*\]\s*", "", line.strip())
        low = body.lower()
        if low.startswith("directshow video devices"):
            section = "video"
            continue
        if low.startswith("directshow audio devices"):
            section = "audio"
            continue
        if low.startswith("alternative name"):
            continue
        m = _DSHOW_NAME.match(body)
        if not m:
            continue
        kind = m.group(2) or section
        if kind in out:
            out[kind].append(m.group(1))
    return out


def dshow_devices(ffmpeg: str) -> dict:
    return parse_dshow_devices(_run_capture(
        [ffmpeg, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"]))


def avfoundation_devices(ffmpeg: str) -> dict:
    return parse_avfoundation_devices(_run_capture(
        [ffmpeg, "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""]))


def list_screens(os_name: str | None = None, ffmpeg: str | None = None) -> dict:
    """Screens ``--capture screen`` can grab: {"available", "screens": [{"index","name"}], "note"}.

    Linux only looks at ``DISPLAY`` (x11grab); macOS parses the avfoundation
    device list ("Capture screen N"); Windows records the whole desktop with
    gdigrab and lists any dshow screen-capture sources next to it."""
    os_name = os_name or platform.system()
    if os_name == "Linux":
        disp = os.environ.get("DISPLAY")
        if not disp:
            return {"available": False, "screens": [],
                    "note": "DISPLAY is not set (no X11 display for x11grab); use --capture screencast"}
        return {"available": True, "screens": [{"index": 0, "name": f"X display {disp} (x11grab)"}],
                "note": None}
    ffmpeg = ffmpeg or _find_ffmpeg()
    if os_name == "Windows":
        screens = [{"index": 0, "name": "desktop (gdigrab, whole desktop)"}]
        if not ffmpeg:
            return {"available": True, "screens": screens, "note": "ffmpeg not found: dshow listing unavailable"}
        try:
            devs = dshow_devices(ffmpeg)
        except Exception as e:  # noqa: BLE001
            return {"available": True, "screens": screens, "note": f"dshow listing unavailable: {_one_line(e)}"}
        for name in devs.get("video", []):
            if "screen" in name.lower():
                screens.append({"index": len(screens), "name": f"{name} (dshow)"})
        return {"available": True, "screens": screens, "note": None}
    if os_name == "Darwin":
        if not ffmpeg:
            return {"available": False, "screens": [], "note": "ffmpeg not found: avfoundation listing unavailable"}
        try:
            devs = avfoundation_devices(ffmpeg)
        except Exception as e:  # noqa: BLE001
            return {"available": False, "screens": [], "note": f"avfoundation listing unavailable: {_one_line(e)}"}
        screens = [{"index": idx, "name": name} for idx, name in devs.get("video", [])
                   if name.lower().startswith("capture screen")]
        note = None if screens else ("no 'Capture screen' device listed (grant Screen Recording "
                                     "permission to the terminal in System Settings > Privacy)")
        return {"available": bool(screens), "screens": screens, "note": note}
    return {"available": False, "screens": [], "note": f"screen listing not supported on {os_name}"}


def _print_devices(audio: dict, screens: dict) -> None:
    _say("audio inputs (sounddevice):")
    if not audio["available"]:
        _say(f"  unavailable: {audio['note']}")
    elif not audio["devices"]:
        _say(f"  none: {audio['note']}")
    for d in audio["devices"]:
        mark = "*" if d["default"] else " "
        _say(f"  [{d['index']}] {mark} {d['name']} ({d['channels']} ch)")
    _say("screens (--capture screen):")
    if not screens["available"] and not screens["screens"]:
        _say(f"  unavailable: {screens['note']}")
    for s in screens["screens"]:
        _say(f"  [{s['index']}] {s['name']}")
    if screens["screens"] and screens.get("note"):
        _say(f"  note: {screens['note']}")


# --------------------------------------------------------------------------- recording backends


def _sd_rec(sd, seconds: float, device: int | None, sr: int) -> np.ndarray:
    kw = {"samplerate": sr, "channels": 1, "dtype": "float32"}
    if device is not None:
        kw["device"] = device
    buf = sd.rec(round(seconds * sr), **kw)
    sd.wait()
    return np.asarray(buf)


def prime_input(sd, device: int | None = None, sr: int = SAMPLE_RATE) -> bool:
    """Open and close the input once, before the passage and the countdown.

    The first stream a terminal opens triggers the macOS microphone permission
    dialog (and driver start-up everywhere); doing it here means it does not
    happen while the user is already reading.  Never raises."""
    stream_cls = getattr(sd, "InputStream", None)
    if stream_cls is None:
        return False
    kw = {"samplerate": sr, "channels": 1, "dtype": "float32"}
    if device is not None:
        kw["device"] = device
    try:
        with stream_cls(**kw):
            pass
        return True
    except Exception:  # noqa: BLE001 - the real attempt below reports the error
        return False


def record_sounddevice(seconds: float, device: int | None = None, sr: int = SAMPLE_RATE,
                       sd=None) -> tuple[np.ndarray, int]:
    """Blocking mono float32 capture through PortAudio; returns ``(audio, native_rate)``.

    Opens the input at ``sr``; when the device refuses that rate (CoreAudio does
    not resample for PortAudio, so a 44.1 kHz-only USB microphone fails) it is
    retried once at the device's ``default_samplerate`` and the result is
    resampled to ``sr``.  Raises RecordError on any failure."""
    if sd is None:
        sd, note = _import_sounddevice_detail()
        if sd is None:
            raise RecordError(f"sounddevice not importable: {note}")
    try:
        return _sd_rec(sd, seconds, device, sr), sr
    except Exception as e:  # noqa: BLE001 - PortAudioError and friends
        first = _one_line(e)
    native = _device_default_rate(sd, device)
    if not native or native == sr:
        raise RecordError(f"sounddevice: {first}")
    try:
        buf = _sd_rec(sd, seconds, device, native)
    except Exception as e:  # noqa: BLE001
        raise RecordError(f"sounddevice: {first}; at {native} Hz: {_one_line(e)}") from None
    return _resample(to_mono(buf), native, sr).reshape(-1, 1), native


def ffmpeg_record_args(ffmpeg: str, fmt: str, device: str | None, seconds: float, out_path: Path,
                       sr: int = SAMPLE_RATE) -> list[str]:
    """ffmpeg argv recording ``seconds`` of mono PCM16 from an OS audio input (pure).

    ``fmt``: dshow (device = DirectShow audio device name), avfoundation (device =
    audio device index or name; default ``-i :default`` = the system default input,
    not the first enumerated device), pulse / alsa (device default ``default``)."""
    head = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
    if fmt == "dshow":
        if not device:
            raise ValueError("dshow needs an audio device name")
        inp = ["-f", "dshow", "-i", f"audio={device}"]
    elif fmt == "avfoundation":
        inp = ["-f", "avfoundation", "-i", f":{device if device not in (None, '') else 'default'}"]
    elif fmt in ("pulse", "alsa"):
        inp = ["-f", fmt, "-i", device or "default"]
    else:
        raise ValueError(f"unknown ffmpeg input format: {fmt}")
    return head + inp + ["-t", f"{float(seconds):g}", "-ac", "1", "-ar", str(sr),
                         "-c:a", "pcm_s16le", str(out_path)]


def ffmpeg_candidates(os_name: str, device: str | None = None, ffmpeg: str | None = None) -> list[tuple[str, str | None]]:
    """(fmt, device) pairs to try in order for this OS; Windows resolves the dshow name via the listing."""
    if os_name == "Windows":
        if device:
            return [("dshow", device)]
        names = dshow_devices(ffmpeg).get("audio", []) if ffmpeg else []
        return [("dshow", n) for n in names[:1]]
    if os_name == "Darwin":
        return [("avfoundation", device)]
    return [("pulse", device), ("alsa", device)]


def ffmpeg_error_summary(stderr: str, returncode: int) -> str:
    """One informative line from ffmpeg's stderr: the generic ``Error opening input
    file(s)`` trailer hides lines such as ``Unknown input format: 'pulse'``."""
    lines = [ln.strip() for ln in (stderr or "").splitlines() if ln.strip()]
    informative = [ln for ln in lines if "Error opening input" not in ln]
    if informative:
        return informative[-1][:160]
    return (lines[-1][:160] if lines else f"exit {returncode}")


def record_ffmpeg(seconds: float, out_path: Path, device: str | None = None, os_name: str | None = None,
                  ffmpeg: str | None = None, sr: int = SAMPLE_RATE,
                  candidates: list | None = None) -> tuple[np.ndarray, str]:
    """Record via ffmpeg into ``<out>.raw.wav`` and load it; returns (audio, "<fmt>:<device>").

    ``candidates`` (from ``prepare_capture``) skips the device listing here."""
    import soundfile as sf

    os_name = os_name or platform.system()
    ffmpeg = ffmpeg or _find_ffmpeg()
    if not ffmpeg:
        raise RecordError("ffmpeg not found (set DEMO_SMOKE_FFMPEG or pip install imageio-ffmpeg)")
    if candidates is None:
        candidates = ffmpeg_candidates(os_name, device, ffmpeg)
    tmp = out_path.with_name(out_path.stem + ".raw.wav")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tried = []
    for fmt, dev in candidates:
        argv = ffmpeg_record_args(ffmpeg, fmt, dev, seconds, tmp, sr)
        try:
            cp = _run_record(argv, timeout=seconds + 60)
        except subprocess.TimeoutExpired as e:
            tried.append(f"{fmt}: ffmpeg timed out after {float(e.timeout):g} s")
            _unlink(tmp)   # a killed ffmpeg leaves a partial capture behind
            continue
        except (OSError, subprocess.SubprocessError) as e:
            tried.append(f"{fmt}: {_one_line(e)}")
            _unlink(tmp)
            continue
        if cp.returncode == 0 and tmp.is_file() and tmp.stat().st_size > 44:
            try:
                data, got_sr = sf.read(str(tmp), dtype="float32", always_2d=True)
            except Exception as e:  # noqa: BLE001
                tried.append(f"{fmt}: unreadable capture ({_one_line(e)})")
                continue
            finally:
                _unlink(tmp)
            if got_sr != sr:
                data = _resample(data[:, 0], got_sr, sr).reshape(-1, 1)
            return data, f"{fmt}:{dev or 'default'}"
        tried.append(f"{fmt}: {ffmpeg_error_summary(cp.stderr, cp.returncode)}")
        _unlink(tmp)
    if not tried:
        raise RecordError("ffmpeg: no dshow audio device listed (run `devices`, or pass --device NAME)")
    raise RecordError("ffmpeg could not record: " + "; ".join(tried))


def _unlink(p: Path) -> None:
    try:
        p.unlink()
    except OSError:
        pass


def _resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    """Linear resampling (only reached when ffmpeg ignored ``-ar``)."""
    n_out = round(x.size * sr_out / float(sr_in))
    if x.size < 2 or n_out < 2:
        return np.asarray(x, dtype=np.float32)
    src = np.linspace(0.0, 1.0, x.size)
    dst = np.linspace(0.0, 1.0, n_out)
    return np.interp(dst, src, x).astype(np.float32)


def prepare_capture(backend: str, device: str | None, os_name: str | None = None) -> dict:
    """Resolve the backend *before* the passage and the countdown; returns a plan for ``run_capture``.

    ``auto`` = sounddevice when it imports (the input is primed once so the macOS
    microphone prompt and driver start-up do not eat the first seconds of the
    reading), else ffmpeg.  The ffmpeg device list (a ``-list_devices`` run on
    Windows) is resolved here too.  Raises RecordError when nothing can record."""
    os_name = os_name or platform.system()
    sd_device: int | None = None
    if device is not None and re.fullmatch(r"-?\d+", str(device).strip()):
        sd_device = int(str(device).strip())
    plan: dict = {"requested": backend, "backend": None, "sd": None, "sd_device": sd_device, "device": device,
                  "os_name": os_name, "ffmpeg": None, "candidates": None, "ffmpeg_device": None, "errors": []}
    if backend in ("auto", "sounddevice"):
        if device is None or sd_device is not None:
            sd, note = _import_sounddevice_detail()
            if sd is not None:
                plan["backend"], plan["sd"] = "sounddevice", sd
                opened = prime_input(sd, sd_device)
                _say("  backend: sounddevice" + (f" device {sd_device}" if sd_device is not None else "")
                     + (" (input opened)" if opened else ""))
                return plan
            msg = f"sounddevice not importable: {note}"
            plan["errors"].append(msg)
            if backend == "sounddevice":
                raise RecordError(msg)
            _say(f"  sounddevice unavailable ({msg}); falling back to ffmpeg")
        elif backend == "sounddevice":
            raise RecordError(f"--device must be a sounddevice index for --backend sounddevice, got {device!r}")
    _prepare_ffmpeg(plan)
    return plan


def _prepare_ffmpeg(plan: dict) -> None:
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        raise RecordError("; ".join(plan["errors"]
                                    + ["ffmpeg not found (set DEMO_SMOKE_FFMPEG or pip install imageio-ffmpeg)"]))
    # A numeric --device is a sounddevice (PortAudio) index; ffmpeg's dshow / avfoundation /
    # pulse namespaces would read the same number as something else, so ffmpeg records from
    # the OS default input instead.
    dev = None if plan["sd_device"] is not None else plan["device"]
    candidates = ffmpeg_candidates(plan["os_name"], dev, ffmpeg)
    if not candidates:
        raise RecordError("; ".join(plan["errors"]
                                    + ["ffmpeg: no dshow audio device listed (run `devices`, or pass --device NAME)"]))
    plan.update({"backend": "ffmpeg", "ffmpeg": ffmpeg, "candidates": candidates, "ffmpeg_device": dev})
    if dev is None and plan["device"] is not None:
        _say(f"  --device {plan['device']} is a sounddevice index, not an ffmpeg device: "
             "ffmpeg records from the OS default input")
    elif dev is None and candidates[0][0] == "dshow":
        _say(f"  ffmpeg: recording from dshow device {candidates[0][1]!r}")


def run_capture(plan: dict, seconds: float, out_path: Path) -> tuple[np.ndarray, str, int]:
    """Record according to ``plan``; returns (audio, backend used, native sample rate)."""
    if plan["backend"] == "sounddevice":
        try:
            data, native = record_sounddevice(seconds, plan["sd_device"], sd=plan["sd"])
            return data, "sounddevice", native
        except RecordError as e:
            plan["errors"].append(str(e))
            if plan["requested"] == "sounddevice":
                raise RecordError("; ".join(plan["errors"])) from None
            _say(f"  sounddevice failed ({e}); falling back to ffmpeg")
            _prepare_ffmpeg(plan)
            _say("  Recording with ffmpeg now: start reading again from part 1.")
    try:
        data, used = record_ffmpeg(seconds, out_path, plan["ffmpeg_device"], plan["os_name"], plan["ffmpeg"],
                                   candidates=plan["candidates"])
        return data, f"ffmpeg ({used})", SAMPLE_RATE
    except RecordError as e:
        plan["errors"].append(str(e))
    raise RecordError("; ".join(plan["errors"]))


# --------------------------------------------------------------------------- commands


def countdown(enabled: bool = True) -> None:
    if not enabled:
        return
    for n in (3, 2, 1):
        _say(f"  {n}...")
        _sleep(1.0)


def _sidecar(out: Path) -> Path:
    return out.with_suffix(".json")


def _write_sidecar(out: Path, data: dict) -> Path:
    p = _sidecar(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return p


def record_ref(out: Path, seconds: float = DEFAULT_SECONDS, device: str | None = None,
               backend: str = "auto", show_countdown: bool = True, os_name: str | None = None) -> dict:
    """Full record-ref flow; returns the sidecar dict (with ``exit_code``).  Raises RecordError."""
    out = Path(out)
    plan = prepare_capture(backend, device, os_name)   # imports, listings, mic permission: before the reading
    chunks = passage_chunks()
    print_passage(chunks)
    _say(f"Recording {seconds:g} s of mono {SAMPLE_RATE // 1000} kHz audio"
         + (f" from device {device}" if device is not None else "") + f" with {plan['backend']}. Get ready.")
    countdown(show_countdown)
    _say("  Recording... speak now.")
    t0 = time.time()
    raw, used, native = run_capture(plan, seconds, out)
    took = round(time.time() - t0, 1)
    _say(f"  done ({took} s, backend {used}); processing...")
    audio, stats = process(raw, SAMPLE_RATE)
    p = write_wav16(out, audio, SAMPLE_RATE)
    warnings = stats.pop("warnings")
    data = {"path": str(p), "sample_rate": SAMPLE_RATE, "channels": 1, "format": "PCM_16",
            "backend": used, "device": device, "native_sample_rate": native, "seconds_requested": float(seconds),
            "seconds_recording": took, **stats, "warnings": warnings,
            "exit_code": EXIT_BAD_INPUT if warnings else EXIT_OK}
    data["json"] = str(_write_sidecar(out, data))
    return data


def _summary(data: dict) -> str:
    warns = data.get("warnings") or []
    return (f"record-ref: {'WARN' if warns else 'ok'} {data['path']} duration={data['duration']}s "
            f"speech={data['speech_seconds']}s peak={data['peak_dbfs']}dBFS rms={data['rms_dbfs']}dBFS "
            f"noise_floor={data['noise_floor_dbfs']}dBFS snr={data['snr_db']}dB "
            f"clipped={data['clipped_pct']}%" + (f" warnings: {'; '.join(warns)}" if warns else ""))


def cmd_record_ref(args) -> int:
    """Handler for ``record-ref``; never raises (``--out`` is a file, not a log directory)."""
    try:
        if getattr(args, "script_only", False):
            print_passage()
            return EXIT_OK
        if getattr(args, "list_devices", False):
            audio = list_input_devices()
            _print_devices(audio, {"available": False, "screens": [], "note": "not probed (--list-devices)"})
            return EXIT_OK
        out = getattr(args, "out", None)
        if not out:
            _err("record-ref: --out voices/<name>.wav is required (or use --script-only / --list-devices)")
            return EXIT_BAD_INPUT
        out = Path(out)
        if out.suffix.lower() != ".wav":
            _err(f"record-ref: --out must end in .wav, got {out}")
            return EXIT_BAD_INPUT
        seconds = getattr(args, "seconds", None)
        seconds = DEFAULT_SECONDS if seconds is None else float(seconds)
        if seconds <= 0:
            _err("record-ref: --seconds must be positive")
            return EXIT_BAD_INPUT
        data = record_ref(out, seconds, getattr(args, "device", None), getattr(args, "backend", "auto") or "auto",
                          show_countdown=not getattr(args, "no_countdown", False))
        _say(_summary(data))
        if WARN_SILENT in data["warnings"]:
            _say("  no signal reached the microphone input: check that the input is not muted and that "
                 "--device names the right microphone (`python -m demo_smoke devices`)")
            if platform.system() == "Darwin":
                _say("  macOS: System Settings > Privacy & Security > Microphone: allow your terminal "
                     "(or OpenCode), then record again")
        elif data["warnings"]:
            _say("  re-record in a quieter room / closer to the mic / at a lower gain, then run "
                 f"`python -m demo_smoke voice-check --ref {data['path']}`")
        return int(data["exit_code"])
    except KeyboardInterrupt:
        _err("record-ref: interrupted")
        return EXIT_INTERRUPTED
    except RecordError as e:
        _err(f"record-ref: {e}")
        return EXIT_ERROR
    except Exception as e:  # one line, exit 3, never a traceback past the CLI
        if os.environ.get("DEMO_SMOKE_DEBUG"):
            raise
        _err(f"record-ref: {_one_line(e)}")
        return EXIT_ERROR


def cmd_devices(args) -> int:
    """Handler for ``devices``: informational, exit 0 even when a probe is unavailable."""
    try:
        audio = list_input_devices()
        screens = list_screens()
        _say(f"devices: {len(audio['devices'])} audio input(s), {len(screens['screens'])} screen(s)")
        _print_devices(audio, screens)
        out = getattr(args, "out", None)
        if out:
            try:
                from .env import Paths

                p = Paths(out).logs / "devices.json"
                p.write_text(json.dumps({"audio": audio, "screens": screens}, indent=2, default=str),
                             encoding="utf-8")
            except OSError as e:
                _say(f"  note: could not write devices.json: {_one_line(e)}")
        return EXIT_OK
    except KeyboardInterrupt:
        _err("devices: interrupted")
        return EXIT_INTERRUPTED
    except Exception as e:
        if os.environ.get("DEMO_SMOKE_DEBUG"):
            raise
        _err(f"devices: {_one_line(e)}")
        return EXIT_ERROR


def register(subparsers, run_map: dict) -> None:
    """Add ``record-ref`` and ``devices`` to an argparse subparsers object; fill ``run_map``."""
    sp = subparsers.add_parser("record-ref", help="record a reference voice for cloning (60 s reading)")
    sp.add_argument("--out", default=None, metavar="voices/NAME.wav",
                    help="WAV to write (stats go next to it as NAME.json)")
    sp.add_argument("--seconds", type=float, default=DEFAULT_SECONDS, help="recording length (default 60)")
    sp.add_argument("--device", default=None,
                    help="sounddevice input index (see `devices`), or an ffmpeg device name/index")
    sp.add_argument("--backend", choices=BACKENDS, default="auto",
                    help="auto = sounddevice, then ffmpeg (dshow/avfoundation/pulse/alsa)")
    sp.add_argument("--list-devices", action="store_true", help="list audio inputs and exit")
    sp.add_argument("--script-only", action="store_true", help="print the reading passage and exit")
    sp.add_argument("--no-countdown", action="store_true", help="skip the 3-2-1 countdown")
    sp.set_defaults(fn=cmd_record_ref)
    run_map["record-ref"] = cmd_record_ref

    sp = subparsers.add_parser("devices", help="list audio inputs and screens")
    sp.add_argument("--out", default="demo-output", help="output directory for logs/devices.json")
    sp.set_defaults(fn=cmd_devices)
    run_map["devices"] = cmd_devices
