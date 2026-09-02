"""Text-to-speech backends.

* ``tone``   synthetic 220 Hz tone with 8 Hz amplitude modulation, no ML deps,
             duration = max(0.8, words / 2.5) s; for tests and dry runs.
* ``turbo``  chatterbox.tts_turbo.ChatterboxTurboTTS
* ``nano``   same class with ``nano=True`` (CPU-friendly).  Only chatterbox-tts
             builds from git ship it; the PyPI releases (<= 0.1.7) do not.
* ``classic`` chatterbox.tts.ChatterboxTTS (uses exaggeration / cfg_weight)
* ``auto``   cuda/rocm/mps -> turbo; otherwise nano when the installed
             chatterbox has it, else turbo (works on CPU, slowly)

Chatterbox is imported lazily inside functions; the loaded model is cached per
process in ``_MODELS``.  Offline env vars are set before import unless
``online=True``.
"""

from __future__ import annotations

import importlib
import inspect
import json
import math
import os
import re
from pathlib import Path

import numpy as np

SR = 24000
BACKENDS = ("auto", "turbo", "nano", "classic", "tone")
TONE_HZ = 220.0
TONE_AM_HZ = 8.0
TONE_PEAK_DBFS = -20.0
WORDS_PER_SECOND = 2.5
MIN_SECONDS = 0.8
INSTALL_HINT = "pip install -r requirements-tts.txt (see README for torch index URLs)"
NANO_HINT = ("the installed chatterbox-tts has no Nano model (PyPI releases up to 0.1.7 ship "
             "Turbo/classic only); install chatterbox-tts from git (see README 'TTS model choice') "
             "or use --tts turbo / --tts classic")
# Exception class names huggingface_hub raises for a missing/offline snapshot.
_HF_CACHE_ERRORS = ("LocalEntryNotFoundError", "OfflineModeIsEnabled", "EntryNotFoundError",
                    "RepositoryNotFoundError", "RevisionNotFoundError", "HfHubHTTPError",
                    "GatedRepoError")
_HF_CACHE_RE = re.compile(r"(?i)cache|offline|huggingface|hf_hub|snapshot|cannot reach|not found")

_MODELS: dict = {}


class TTSError(RuntimeError):
    """Backend unavailable or synthesis failed (pipeline error, CLI exit 3)."""


# --------------------------------------------------------------------------- helpers


def resolve_backend(backend: str) -> str:
    b = (backend or "auto").lower()
    if b not in BACKENDS:
        raise TTSError(f"unknown --tts backend '{backend}'; choose one of {', '.join(BACKENDS)}")
    if b != "auto":
        return b
    from .env import chatterbox_nano_supported, torch_device

    if torch_device() in ("cuda", "rocm", "mps"):
        return "turbo"
    # CPU (or no torch yet): Nano is the CPU model, but only git builds of
    # chatterbox-tts ship it; the PyPI wheels fall back to Turbo.
    return "nano" if chatterbox_nano_supported() else "turbo"


def set_offline_env(online: bool = False) -> None:
    if online:
        return
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


def to_numpy(wav) -> np.ndarray:
    """Accept torch tensors, numpy arrays or lists; return float32 mono 1-D."""
    if hasattr(wav, "detach"):
        wav = wav.detach()
    if hasattr(wav, "cpu"):
        wav = wav.cpu()
    if hasattr(wav, "numpy") and not isinstance(wav, np.ndarray):
        wav = wav.numpy()
    arr = np.asarray(wav)
    if arr.dtype.kind in "iu":
        arr = arr.astype(np.float32) / float(np.iinfo(arr.dtype).max)
    arr = arr.astype(np.float32)
    if arr.ndim == 0:
        arr = arr.reshape(1)
    while arr.ndim > 1:
        # (channels, samples) or (samples, channels) or (1, samples): average the small axis
        axis = int(np.argmin(arr.shape))
        arr = arr.mean(axis=axis).astype(np.float32)
    return np.ascontiguousarray(arr)


def audio_stats(wav, sr: int) -> dict:
    a = to_numpy(wav)
    n = int(a.size)
    duration = n / float(sr) if sr else 0.0
    if n == 0:
        return {"duration": 0.0, "peak_dbfs": -math.inf, "rms_dbfs": -math.inf,
                "silent": True, "clipped": False}
    peak = float(np.max(np.abs(a)))
    rms = float(np.sqrt(np.mean(a.astype(np.float64) ** 2)))
    peak_db = 20 * math.log10(peak) if peak > 0 else -math.inf
    rms_db = 20 * math.log10(rms) if rms > 0 else -math.inf
    clipped_frac = float(np.mean(np.abs(a) >= 0.999))
    return {
        "duration": round(duration, 3),
        "peak_dbfs": round(peak_db, 2) if math.isfinite(peak_db) else -120.0,
        "rms_dbfs": round(rms_db, 2) if math.isfinite(rms_db) else -120.0,
        "silent": bool(rms_db < -50),
        "clipped": bool(clipped_frac > 0.005),
    }


def write_wav(path: str | Path, wav, sr: int) -> Path:
    import soundfile as sf

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    a = np.clip(to_numpy(wav), -1.0, 1.0)
    sf.write(str(p), a, int(sr), subtype="PCM_16")
    return p


def _count_words(text: str) -> int:
    return sum(1 for w in str(text).split() if any(c.isalnum() for c in w))


# --------------------------------------------------------------------------- tone backend


def tone(text: str, sr: int = SR) -> tuple[np.ndarray, int]:
    """Obviously-synthetic placeholder narration: 220 Hz, 8 Hz tremolo, -20 dBFS peak."""
    seconds = max(MIN_SECONDS, _count_words(text) / WORDS_PER_SECOND)
    n = round(seconds * sr)
    t = np.arange(n, dtype=np.float64) / sr
    carrier = np.sin(2 * math.pi * TONE_HZ * t)
    tremolo = 0.7 + 0.3 * np.sin(2 * math.pi * TONE_AM_HZ * t)
    env = np.ones(n)
    fade = min(int(0.02 * sr), max(n // 4, 1))
    ramp = np.linspace(0.0, 1.0, fade)
    env[:fade] = ramp
    env[n - fade:] = ramp[::-1]
    amp = 10 ** (TONE_PEAK_DBFS / 20)
    wav = (amp * carrier * tremolo * env).astype(np.float32)
    return wav, sr


# --------------------------------------------------------------------------- chatterbox backends


def _import(module: str):
    try:
        return importlib.import_module(module)
    except ImportError as e:
        raise TTSError(
            f"cannot import {module} ({e}). Voice backends need chatterbox-tts + torch: "
            f"{INSTALL_HINT}; or use --tts tone"
        ) from None


def _device(device: str | None) -> str:
    if device:
        return device
    from .env import torch_device

    d = torch_device()
    if d == "none":
        raise TTSError(f"torch is not installed; {INSTALL_HINT}; or use --tts tone")
    return "cuda" if d == "rocm" else d


def _accepts_nano(cls) -> bool:
    try:
        return "nano" in inspect.signature(cls.from_pretrained).parameters
    except (TypeError, ValueError):
        return False


def _looks_like_cache_miss(exc: BaseException) -> bool:
    """Is this a Hugging Face cache/offline error (as opposed to any other failure)?"""
    for e in (exc, getattr(exc, "__cause__", None), getattr(exc, "__context__", None)):
        if e is None:
            continue
        if e.__class__.__name__ in _HF_CACHE_ERRORS:
            return True
        if isinstance(e, (FileNotFoundError, OSError)) and _HF_CACHE_RE.search(str(e) or ""):
            return True
    return False


def load_model(backend: str, device: str | None = None, online: bool = False):
    """Load (once per process) and return the chatterbox model for ``backend``."""
    backend = resolve_backend(backend)
    if backend == "tone":
        return None
    set_offline_env(online)
    dev = _device(device)
    key = (backend, dev)
    if key in _MODELS:
        return _MODELS[key]
    try:
        if backend in ("turbo", "nano"):
            mod = _import("chatterbox.tts_turbo")
            cls = getattr(mod, "ChatterboxTurboTTS", None)
            if cls is None:
                raise TTSError("chatterbox.tts_turbo has no ChatterboxTurboTTS; upgrade chatterbox-tts")
            if backend == "nano":
                if not _accepts_nano(cls):
                    raise TTSError(NANO_HINT)
                model = cls.from_pretrained(device=dev, nano=True)
            else:
                model = cls.from_pretrained(device=dev)
        else:
            mod = _import("chatterbox.tts")
            cls = getattr(mod, "ChatterboxTTS", None)
            if cls is None:
                raise TTSError("chatterbox.tts has no ChatterboxTTS; upgrade chatterbox-tts")
            model = cls.from_pretrained(device=dev)
    except TTSError:
        raise
    except Exception as e:  # noqa: BLE001 - HF/torch raise many types; surface one line
        hint = ""
        if _looks_like_cache_miss(e):
            hint = (" Weights missing from the HF cache? run `python -m demo_smoke prefetch "
                    f"--tts {backend}` while online" + (
                        "." if os.environ.get("HF_HUB_OFFLINE") == "1" else
                        " (or pass --online to download now)."))
        raise TTSError(f"failed to load chatterbox '{backend}' on {dev}: {e}.{hint}") from None
    _MODELS[key] = model
    return model


def synthesize(text: str, ref_wav: Path | None, backend: str = "auto", device: str | None = None,
               exaggeration: float = 0.5, cfg_weight: float = 0.5,
               online: bool = False) -> tuple[np.ndarray, int]:
    """Return (float32 mono samples, sample rate) for ``text``."""
    backend = resolve_backend(backend)
    if backend == "tone":
        return tone(text)
    if ref_wav is not None and not Path(ref_wav).is_file():
        raise TTSError(f"reference voice not found: {ref_wav}")
    model = load_model(backend, device, online)
    kwargs: dict = {}
    if ref_wav is not None:
        kwargs["audio_prompt_path"] = str(ref_wav)
    if backend == "classic":
        kwargs["exaggeration"] = float(exaggeration)
        kwargs["cfg_weight"] = float(cfg_weight)
    try:
        wav = model.generate(text, **kwargs)
    except Exception as e:  # noqa: BLE001 - torch/model errors of any kind
        raise TTSError(f"chatterbox '{backend}' failed on {text[:40]!r}: {e}") from None
    sr = int(getattr(model, "sr", SR) or SR)
    return to_numpy(wav), sr


def synth_all(out: Path, ref_wav: Path | None, backend: str = "auto", online: bool = False,
              device: str | None = None) -> dict:
    """Synthesize every segment of ``audio/narration.json`` into ``audio/seg-*.wav``.

    Writes ``audio/durations.json`` ({"intro": s, "<id>": s, ..., "outro": s})
    and returns it.
    """
    audio = Path(out) / "audio"
    narr_path = audio / "narration.json"
    if not narr_path.is_file():
        raise TTSError(f"{narr_path} not found: run narrate-template or narrate-llm first")
    try:
        narr = json.loads(narr_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise TTSError(f"{narr_path} is not valid JSON: {e}") from None
    segments: list[tuple[str, str]] = [("intro", narr.get("intro", ""))]
    for st in narr.get("steps", []):
        segments.append((st["id"], st.get("text", "")))
    segments.append(("outro", narr.get("outro", "")))
    resolved = resolve_backend(backend)
    durations: dict = {}
    stats: dict = {}
    for sid, text in segments:
        wav, sr = synthesize(text, ref_wav, resolved, device=device, online=online)
        p = write_wav(audio / f"seg-{sid}.wav", wav, sr)
        st = audio_stats(wav, sr)
        durations[sid] = st["duration"]
        stats[sid] = {**st, "path": str(p), "words": _count_words(text)}
    (audio / "durations.json").write_text(json.dumps(durations, indent=2), encoding="utf-8")
    (audio / "synth-stats.json").write_text(
        json.dumps({"backend": resolved, "segments": stats}, indent=2), encoding="utf-8")
    return durations
