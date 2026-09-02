import sys
import types
from typing import ClassVar

import numpy as np
import pytest
import soundfile as sf

from demo_smoke import tts


@pytest.fixture(autouse=True)
def _clear_cache():
    tts._MODELS.clear()
    yield
    tts._MODELS.clear()


# --------------------------------------------------------------------------- tone backend


def test_tone_shape_and_level():
    wav, sr = tts.synthesize("one two three four five six seven eight nine ten", None, "tone")
    assert sr == 24000
    assert wav.dtype == np.float32 and wav.ndim == 1
    assert len(wav) == round(10 / 2.5 * 24000)
    st = tts.audio_stats(wav, sr)
    assert st["duration"] == pytest.approx(4.0, abs=0.01)
    assert st["peak_dbfs"] == pytest.approx(-20.0, abs=0.3)
    assert -30 < st["rms_dbfs"] < -20
    assert st["silent"] is False and st["clipped"] is False


def test_tone_minimum_and_modulation():
    wav, sr = tts.tone("hi")
    assert len(wav) == round(0.8 * sr)
    # 8 Hz tremolo: the envelope of 20 ms slices should vary noticeably within one second
    slices = np.abs(wav[: sr]).reshape(-1, sr // 50).max(axis=1)
    assert slices.max() > 1.5 * slices[5:-5].min()
    # dominant frequency ~ 220 Hz
    spec = np.abs(np.fft.rfft(wav * np.hanning(len(wav))))
    freqs = np.fft.rfftfreq(len(wav), 1 / sr)
    assert abs(freqs[int(np.argmax(spec))] - 220) < 10


def test_audio_stats_silent_and_clipped():
    sr = 24000
    st = tts.audio_stats(np.zeros(sr, np.float32), sr)
    assert st["silent"] is True and st["peak_dbfs"] == -120.0
    loud = np.ones(sr, np.float32)
    st = tts.audio_stats(loud, sr)
    assert st["clipped"] is True and st["peak_dbfs"] == 0.0
    st = tts.audio_stats(np.array([], np.float32), sr)
    assert st["silent"] is True and st["duration"] == 0.0


def test_to_numpy_accepts_tensor_like_and_multichannel():
    class T:
        def __init__(self, a):
            self.a = a

        def detach(self):
            return self

        def cpu(self):
            return self

        def squeeze(self):
            return self

        def numpy(self):
            return self.a

    out = tts.to_numpy(T(np.full((1, 100), 0.25, np.float32)))
    assert out.shape == (100,) and out.dtype == np.float32
    stereo = np.stack([np.ones(50), np.zeros(50)], axis=1)   # (samples, channels)
    assert tts.to_numpy(stereo).shape == (50,)
    assert tts.to_numpy(np.array([16384, -16384], np.int16))[0] == pytest.approx(0.5, abs=0.01)
    assert tts.to_numpy([0.1, 0.2]).tolist() == pytest.approx([0.1, 0.2])


def test_write_wav_pcm16(tmp_path):
    wav, sr = tts.tone("hello there")
    p = tts.write_wav(tmp_path / "a" / "t.wav", wav, sr)
    info = sf.info(str(p))
    assert info.subtype == "PCM_16" and info.samplerate == 24000 and info.channels == 1
    data, _ = sf.read(str(p), dtype="float32")
    assert len(data) == len(wav)


def test_resolve_backend(monkeypatch):
    assert tts.resolve_backend("tone") == "tone"
    assert tts.resolve_backend("TURBO") == "turbo"
    monkeypatch.setattr("demo_smoke.env.torch_device", lambda: "cuda")
    assert tts.resolve_backend("auto") == "turbo"
    monkeypatch.setattr("demo_smoke.env.torch_device", lambda: "cpu")
    assert tts.resolve_backend("auto") == "nano"
    monkeypatch.setattr("demo_smoke.env.torch_device", lambda: "none")
    assert tts.resolve_backend("auto") == "nano"
    with pytest.raises(tts.TTSError, match="unknown --tts backend"):
        tts.resolve_backend("bark")


def test_synth_all_tone(out_dir):
    audio = out_dir / "audio"
    audio.mkdir(parents=True)
    (audio / "narration.json").write_text(
        '{"intro": "one two three", "outro": "bye", '
        '"steps": [{"id": "open", "text": "I open the app now"}, {"id": "ask", "text": "ask"}]}')
    durations = tts.synth_all(out_dir, None, "tone")
    assert list(durations) == ["intro", "open", "ask", "outro"]
    assert durations["intro"] == pytest.approx(1.2, abs=0.01)
    assert durations["open"] == pytest.approx(2.0, abs=0.01)
    assert durations["ask"] == pytest.approx(0.8, abs=0.01)
    for sid in durations:
        assert (audio / f"seg-{sid}.wav").is_file()
    import json
    assert json.loads((audio / "durations.json").read_text()) == durations
    stats = json.loads((audio / "synth-stats.json").read_text())
    assert stats["backend"] == "tone" and stats["segments"]["open"]["words"] == 5


def test_synth_all_missing_narration(out_dir):
    with pytest.raises(tts.TTSError, match="narrate-template"):
        tts.synth_all(out_dir, None, "tone")


# --------------------------------------------------------------------------- chatterbox (mocked)


class FakeTensor:
    def __init__(self, arr):
        self.arr = arr

    def squeeze(self):
        return FakeTensor(np.squeeze(self.arr))

    def detach(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self.arr


def _make_fakes(model_sr=24000, as_numpy=False):
    class FakeTurbo:
        loads: ClassVar[list] = []
        calls: ClassVar[list] = []
        sr = 24000

        @classmethod
        def from_pretrained(cls, device, nano=False):
            inst = cls()
            inst.device, inst.nano = device, nano
            cls.loads.append({"device": device, "nano": nano})
            return inst

        def generate(self, text, **kw):
            self.calls.append({"text": text, **kw})
            arr = np.full((1, 2400), 0.2, np.float32)
            return arr if as_numpy else FakeTensor(arr)

    class FakeClassic(FakeTurbo):
        loads: ClassVar[list] = []
        calls: ClassVar[list] = []
        sr = model_sr

        @classmethod
        def from_pretrained(cls, device):
            inst = cls()
            inst.device = device
            cls.loads.append({"device": device})
            return inst

    pkg = types.ModuleType("chatterbox")
    turbo_mod = types.ModuleType("chatterbox.tts_turbo")
    turbo_mod.ChatterboxTurboTTS = FakeTurbo
    classic_mod = types.ModuleType("chatterbox.tts")
    classic_mod.ChatterboxTTS = FakeClassic
    return pkg, turbo_mod, classic_mod, FakeTurbo, FakeClassic


@pytest.fixture
def fake_chatterbox(monkeypatch):
    pkg, turbo_mod, classic_mod, FakeTurbo, FakeClassic = _make_fakes()
    monkeypatch.setitem(sys.modules, "chatterbox", pkg)
    monkeypatch.setitem(sys.modules, "chatterbox.tts_turbo", turbo_mod)
    monkeypatch.setitem(sys.modules, "chatterbox.tts", classic_mod)
    monkeypatch.setattr("demo_smoke.env.torch_device", lambda: "cpu")
    return FakeTurbo, FakeClassic


def test_turbo_branch(fake_chatterbox, tmp_path, monkeypatch):
    FakeTurbo, _ = fake_chatterbox
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    ref = tmp_path / "ref.wav"
    sf.write(str(ref), np.zeros(2400, np.float32), 24000)
    wav, sr = tts.synthesize("hello", ref, "turbo")
    assert sr == 24000 and wav.shape == (2400,) and wav.dtype == np.float32
    assert wav[0] == pytest.approx(0.2)
    assert FakeTurbo.loads == [{"device": "cpu", "nano": False}]
    assert FakeTurbo.calls[-1] == {"text": "hello", "audio_prompt_path": str(ref)}
    assert "exaggeration" not in FakeTurbo.calls[-1]
    import os
    assert os.environ["HF_HUB_OFFLINE"] == "1" and os.environ["TRANSFORMERS_OFFLINE"] == "1"
    # cached: a second call does not reload
    tts.synthesize("again", None, "turbo")
    assert len(FakeTurbo.loads) == 1
    assert "audio_prompt_path" not in FakeTurbo.calls[-1]


def test_nano_branch_and_device_override(fake_chatterbox):
    FakeTurbo, _ = fake_chatterbox
    tts.synthesize("hello", None, "nano", device="mps")
    assert FakeTurbo.loads == [{"device": "mps", "nano": True}]
    tts.synthesize("hello", None, "auto")           # cpu -> nano, cached with device cpu
    assert FakeTurbo.loads[-1] == {"device": "cpu", "nano": True}
    assert len(tts._MODELS) == 2


def test_classic_branch_passes_style_args(fake_chatterbox):
    _, FakeClassic = fake_chatterbox
    wav, sr = tts.synthesize("hello", None, "classic", exaggeration=0.7, cfg_weight=0.3)
    assert FakeClassic.loads == [{"device": "cpu"}]
    assert FakeClassic.calls[-1] == {"text": "hello", "exaggeration": 0.7, "cfg_weight": 0.3}
    assert sr == 24000 and wav.shape == (2400,)


def test_numpy_output_and_model_sr(monkeypatch):
    pkg, _turbo_mod, classic_mod, _FakeTurbo, FakeClassic = _make_fakes(model_sr=22050, as_numpy=True)
    monkeypatch.setitem(sys.modules, "chatterbox", pkg)
    monkeypatch.setitem(sys.modules, "chatterbox.tts", classic_mod)
    monkeypatch.setattr("demo_smoke.env.torch_device", lambda: "cuda")
    wav, sr = tts.synthesize("x", None, "classic")
    assert sr == 22050 and isinstance(wav, np.ndarray) and wav.ndim == 1
    assert FakeClassic.loads == [{"device": "cuda"}]


def test_online_flag_keeps_env(fake_chatterbox, monkeypatch):
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    tts.synthesize("x", None, "turbo", online=True)
    import os
    assert "HF_HUB_OFFLINE" not in os.environ


def test_synth_all_with_fake_model(fake_chatterbox, out_dir):
    audio = out_dir / "audio"
    audio.mkdir(parents=True)
    (audio / "narration.json").write_text('{"intro": "a", "outro": "b", "steps": [{"id": "s", "text": "c"}]}')
    d = tts.synth_all(out_dir, None, "turbo")
    assert d == {"intro": 0.1, "s": 0.1, "outro": 0.1}
    assert sf.info(str(audio / "seg-s.wav")).samplerate == 24000


def test_missing_chatterbox_gives_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "chatterbox", None)
    monkeypatch.setitem(sys.modules, "chatterbox.tts_turbo", None)
    monkeypatch.setitem(sys.modules, "chatterbox.tts", None)
    monkeypatch.setattr("demo_smoke.env.torch_device", lambda: "cpu")
    with pytest.raises(tts.TTSError, match="requirements-tts.txt") as ei:
        tts.synthesize("x", None, "turbo")
    assert "\n" not in str(ei.value)
    with pytest.raises(tts.TTSError, match="requirements-tts.txt"):
        tts.synthesize("x", None, "classic")


def test_missing_torch_gives_hint(monkeypatch):
    monkeypatch.setattr("demo_smoke.env.torch_device", lambda: "none")
    with pytest.raises(tts.TTSError, match="torch is not installed"):
        tts.synthesize("x", None, "nano")


def test_missing_ref_and_generate_failure(fake_chatterbox, tmp_path):
    with pytest.raises(tts.TTSError, match="reference voice not found"):
        tts.synthesize("x", tmp_path / "nope.wav", "turbo")
    FakeTurbo, _ = fake_chatterbox

    def boom(self, text, **kw):
        raise RuntimeError("CUDA out of memory")

    FakeTurbo.generate = boom
    with pytest.raises(tts.TTSError, match="CUDA out of memory"):
        tts.synthesize("x", None, "turbo")


def test_load_failure_mentions_prefetch_when_offline(monkeypatch):
    pkg, turbo_mod, _classic_mod, FakeTurbo, _ = _make_fakes()

    def fail(cls, device, nano=False):
        raise OSError("model.safetensors not found in cache")

    FakeTurbo.from_pretrained = classmethod(fail)
    monkeypatch.setitem(sys.modules, "chatterbox", pkg)
    monkeypatch.setitem(sys.modules, "chatterbox.tts_turbo", turbo_mod)
    monkeypatch.setattr("demo_smoke.env.torch_device", lambda: "cpu")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    with pytest.raises(tts.TTSError, match="prefetch --tts nano"):
        tts.load_model("nano")
