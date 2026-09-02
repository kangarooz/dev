"""A stand-in for the ``sounddevice`` module (PortAudio is not available here).

Inject with ``monkeypatch.setitem(sys.modules, "sounddevice", tests.fakes.sounddevice)``.
``rec()`` returns a synthetic "reading": speech-like bursts (band-limited noise,
300-3000 Hz, with a 3 Hz syllable envelope) separated by short gaps, padded
with 1 s of silence on each side, over a configurable white-noise floor.

Knobs live in ``config`` (call ``reset()`` between tests):

* ``noise_db``       rms of the noise floor in dBFS (default -65; note record-ref normalises
                     the speech peak to -3 dBFS, so the floor it reports is ~17 dB higher)
* ``speech_db``      peak of the speech bursts in dBFS (default -20; > 0 clips like an ADC)
* ``speech_seconds`` how much of the take contains speech (default: everything but the padding)
* ``pad_s``          silence on each side (default 1.0)
* ``fail``           make ``rec`` raise PortAudioError (to exercise the ffmpeg fallback)
* ``rate_ok``        when set, ``rec`` raises PortAudioError unless ``samplerate`` equals it
                     (a 44.1 kHz-only USB microphone on CoreAudio)
* ``seed``           RNG seed

``InputStream`` records every open in ``primed`` (record-ref primes the input once
before the countdown); ``query_hostapis`` returns one host API.
"""

from __future__ import annotations

import numpy as np

__version__ = "0.0-fake"

DEFAULTS = {"noise_db": -65.0, "speech_db": -20.0, "speech_seconds": None, "pad_s": 1.0,
            "fail": False, "rate_ok": None, "seed": 1234}
config: dict = dict(DEFAULTS)
calls: list[dict] = []
primed: list[dict] = []
HOSTAPIS = [{"name": "Fake Audio", "devices": [0, 1, 2], "default_input_device": 0}]

DEVICES = [
    {"name": "Built-in Microphone", "max_input_channels": 2, "max_output_channels": 0,
     "default_samplerate": 48000.0, "hostapi": 0},
    {"name": "Built-in Output", "max_input_channels": 0, "max_output_channels": 2,
     "default_samplerate": 48000.0, "hostapi": 0},
    {"name": "USB Microphone", "max_input_channels": 1, "max_output_channels": 0,
     "default_samplerate": 44100.0, "hostapi": 0},
]


class PortAudioError(Exception):
    pass


class _Default:
    def __init__(self) -> None:
        self.device = [0, 1]
        self.samplerate = None
        self.channels = None
        self.dtype = None


default = _Default()


def reset() -> None:
    config.clear()
    config.update(DEFAULTS)
    calls.clear()
    primed.clear()
    default.device = [0, 1]


def query_devices(device=None, kind=None):
    if device is not None:
        return dict(DEVICES[int(device)])
    return [dict(d) for d in DEVICES]


def query_hostapis(index=None):
    if index is not None:
        return dict(HOSTAPIS[int(index)])
    return [dict(a) for a in HOSTAPIS]


class InputStream:
    """Opened and closed once by record-ref before the countdown (mic permission / driver start)."""

    def __init__(self, samplerate=None, channels=1, dtype="float32", device=None, **kw):
        primed.append({"samplerate": samplerate, "channels": channels, "dtype": dtype, "device": device})
        if config.get("fail"):
            raise PortAudioError("Error opening InputStream: Invalid device (fake)")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def band_limited_noise(n: int, sr: int, lo: float, hi: float, rng) -> np.ndarray:
    x = rng.standard_normal(n)
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1.0 / sr)
    spec[(f < lo) | (f > hi)] = 0
    y = np.fft.irfft(spec, n=n)
    peak = float(np.max(np.abs(y))) or 1.0
    return y / peak


def synth_recording(seconds: float, sr: int, noise_db: float = -60.0, speech_db: float = -20.0,
                    speech_seconds: float | None = None, pad_s: float = 1.0, seed: int = 1234,
                    burst_s: float = 2.0, gap_s: float = 0.4) -> np.ndarray:
    """Float32 mono (n, 1) take: noise floor + bursts (peak ``speech_db``) + ``pad_s`` silence each side."""
    rng = np.random.default_rng(seed)
    n = round(seconds * sr)
    out = rng.standard_normal(n) * 10 ** (noise_db / 20.0)
    budget = max(0.0, seconds - 2 * pad_s)
    if speech_seconds is not None:
        budget = min(budget, max(0.0, float(speech_seconds)))
    t, t_end = pad_s, pad_s + budget
    amp = 10 ** (speech_db / 20.0)
    while t < t_end - 0.05:
        dur = min(burst_s, t_end - t)
        s, e = int(t * sr), int((t + dur) * sr)
        tt = np.arange(e - s) / sr
        env = 0.55 + 0.45 * np.sin(2 * np.pi * 3.0 * tt)       # 3 Hz "syllables"
        out[s:e] += band_limited_noise(e - s, sr, 300.0, 3000.0, rng) * env * amp
        t += dur + gap_s
    return np.clip(out, -1.0, 1.0).astype(np.float32).reshape(-1, 1)


def rec(frames=None, samplerate=None, channels=1, dtype="float32", device=None, blocking=False, **kw):
    calls.append({"frames": frames, "samplerate": samplerate, "channels": channels,
                  "dtype": dtype, "device": device})
    if config.get("fail"):
        raise PortAudioError("Error opening InputStream: Invalid device (fake)")
    if config.get("rate_ok") and int(samplerate or 0) != int(config["rate_ok"]):
        raise PortAudioError(f"Error opening InputStream: Invalid sample rate (fake, wants {config['rate_ok']})")
    sr = int(samplerate or 48000)
    seconds = float(frames) / sr
    data = synth_recording(seconds, sr, noise_db=config["noise_db"], speech_db=config["speech_db"],
                           speech_seconds=config["speech_seconds"], pad_s=config["pad_s"],
                           seed=config["seed"])
    if channels and channels > 1:
        data = np.repeat(data, channels, axis=1)
    return data


def wait(ignore_errors=True):
    return None
