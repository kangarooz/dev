"""record-ref: passage, DSP helpers, sounddevice path (fake module), ffmpeg fallback, exit codes."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from demo_smoke import onboard_audio as oa
from tests.fakes import sounddevice as fake_sd

SR = oa.SAMPLE_RATE


@pytest.fixture
def fake_sounddevice(monkeypatch):
    fake_sd.reset()
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    monkeypatch.setattr(oa, "_sleep", lambda s: None)
    yield fake_sd
    fake_sd.reset()


@pytest.fixture
def no_sounddevice(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", None)   # import raises ImportError
    monkeypatch.setattr(oa, "_sleep", lambda s: None)


def parse(argv: list[str]):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd")
    run_map: dict = {}
    oa.register(sub, run_map)
    args = p.parse_args(argv)
    return args, run_map


def run(argv: list[str]) -> int:
    args, run_map = parse(argv)
    return run_map[args.cmd](args)


# --------------------------------------------------------------------------- passage


def test_passage_is_about_150_words_in_three_chunks():
    text = oa.passage_text()
    n = len(text.split())
    assert 120 <= n <= 180
    chunks = oa.passage_chunks()
    assert len(chunks) == 3
    assert " ".join(chunks) == " ".join(text.split())
    sizes = [len(c.split()) for c in chunks]
    assert min(sizes) >= 25        # no chunk is a stub
    assert max(sizes) - min(sizes) <= 12    # roughly equal thirds (was 69 / 33 / 45)
    assert "?" in text and "fourteenth" in text     # a question and spoken numbers, per the contract


def test_script_only_prints_passage_and_exits_zero(capsys):
    assert run(["record-ref", "--script-only"]) == 0
    out = capsys.readouterr().out
    assert "part 1/3" in out and "part 3/3" in out
    assert "quick brown fox" in out


# --------------------------------------------------------------------------- dsp helpers


def test_frame_rms_db_levels():
    tone = np.full(SR, 0.5)     # constant 0.5 -> rms -6.02 dBFS
    db = oa.frame_rms_db(tone, SR, 50)
    assert db.shape == (20,)
    assert db == pytest.approx(-6.02, abs=0.01)
    assert oa.frame_rms_db(np.zeros(100), SR)[0] == oa.DB_FLOOR
    assert oa.frame_rms_db(np.zeros(0), SR).size == 0


def test_normalize_peak_hits_minus_3_dbfs_and_leaves_silence():
    x = np.array([0.1, -0.25, 0.05])
    y, gain = oa.normalize_peak(x)
    assert np.max(np.abs(y)) == pytest.approx(10 ** (-3 / 20))
    assert gain == pytest.approx(20 * np.log10(10 ** (-3 / 20) / 0.25), abs=0.01)
    z, g0 = oa.normalize_peak(np.zeros(10))
    assert g0 == 0.0 and not z.any()


def test_trim_silence_keeps_200ms_padding():
    x = np.zeros(5 * SR)
    x[2 * SR: 3 * SR] = 0.5                     # 1 s of "speech" from t=2 to t=3
    y, start, end = oa.trim_silence(x, SR)
    assert start == pytest.approx(2 * SR - 0.2 * SR, abs=SR * 0.02)
    assert end == pytest.approx(3 * SR + 0.2 * SR, abs=SR * 0.02)
    assert y.size == end - start
    # nothing above threshold -> untouched
    q = np.full(SR, 0.001)
    y2, s2, e2 = oa.trim_silence(q, SR)
    assert (s2, e2) == (0, SR) and y2.size == SR


def test_analyze_noise_floor_snr_and_warnings():
    rng = np.random.default_rng(0)
    noise = rng.standard_normal(30 * SR) * 10 ** (-60 / 20)
    speech = noise.copy()
    # 25 s of loud noise-band "speech" (rms about -20 dBFS) in the middle, quiet edges
    speech[2 * SR: 27 * SR] += rng.standard_normal(25 * SR) * 10 ** (-20 / 20)
    st = oa.analyze(speech, SR)
    assert st["noise_floor_dbfs"] == pytest.approx(-60, abs=1.5)
    assert st["speech_rms_dbfs"] == pytest.approx(-20, abs=1.0)
    assert st["snr_db"] == pytest.approx(40, abs=2)
    assert 24 <= st["speech_seconds"] <= 26
    assert oa.warnings_for(st) == []
    st_silent = oa.analyze(noise, SR)
    assert st_silent["speech_seconds"] < 20
    assert oa.WARN_SHORT in oa.warnings_for(st_silent)
    assert oa.warnings_for({"speech_seconds": 30, "snr_db": 10, "clipped": True}) == [oa.WARN_NOISY, oa.WARN_CLIPPED]
    empty = oa.analyze(np.zeros(0), SR)
    assert empty["duration"] == 0.0 and empty["clipped"] is False


def test_clipped_pct():
    x = np.zeros(1000)
    x[:10] = 1.0
    x[10:20] = -1.0
    assert oa.clipped_pct(x) == pytest.approx(2.0)
    assert oa.clipped_pct(np.zeros(0)) == 0.0


def test_to_mono_handles_int16_and_stereo():
    st = np.array([[16384, -16384], [0, 0]], dtype=np.int16)
    m = oa.to_mono(st)
    assert m.shape == (2,) and m[0] == pytest.approx(0.0, abs=1e-6)
    assert oa.to_mono(np.array([[0.5], [0.25]], dtype=np.float32)).tolist() == [0.5, 0.25]


# --------------------------------------------------------------------------- sounddevice path


def test_record_ref_happy_path(fake_sounddevice, tmp_path, capsys):
    out = tmp_path / "voices" / "nick.wav"
    code = run(["record-ref", "--out", str(out), "--seconds", "40", "--no-countdown"])
    printed = capsys.readouterr().out
    assert code == 0, printed
    assert "part 1/3" in printed and "3..." not in printed
    assert "record-ref: ok" in printed
    # captured mono 48 kHz float32 for --seconds
    assert fake_sounddevice.calls == [{"frames": 40 * SR, "samplerate": SR, "channels": 1,
                                       "dtype": "float32", "device": None}]
    # the input was opened once before the passage was printed (macOS permission prompt, driver start)
    assert fake_sounddevice.primed == [{"samplerate": SR, "channels": 1, "dtype": "float32", "device": None}]
    assert printed.index("backend: sounddevice (input opened)") < printed.index("part 1/3")
    data, sr = sf.read(str(out), dtype="float32", always_2d=True)
    info = sf.info(str(out))
    assert sr == SR and info.subtype == "PCM_16" and info.channels == 1
    # trimmed: 1 s padding each side -> ~0.2 s left, and peak at -3 dBFS
    assert 37.9 <= len(data) / SR <= 38.7
    assert 20 * np.log10(np.max(np.abs(data))) == pytest.approx(-3.0, abs=0.1)
    side = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert side["path"] == str(out) and side["backend"] == "sounddevice"
    assert side["sample_rate"] == SR and side["format"] == "PCM_16"
    assert side["warnings"] == [] and side["exit_code"] == 0
    assert side["peak_dbfs"] == pytest.approx(-3.0, abs=0.1)
    # fake floor -65 dBFS + 17 dB normalisation gain -> about -48; speech rms about -20
    assert side["snr_db"] >= 25 and side["noise_floor_dbfs"] < -40
    assert side["speech_seconds"] >= 30 and side["clipped"] is False
    assert side["raw_duration"] == pytest.approx(40.0) and side["trim"]["start_s"] == pytest.approx(0.8, abs=0.05)
    assert side["native_sample_rate"] == SR and side["raw_peak_dbfs"] == pytest.approx(-20.0, abs=0.5)
    for k in ("duration", "peak_dbfs", "rms_dbfs", "noise_floor_dbfs", "snr_db", "clipped_pct"):
        assert k in side


def test_record_ref_countdown_and_device(fake_sounddevice, tmp_path, capsys):
    out = tmp_path / "v.wav"
    code = run(["record-ref", "--out", str(out), "--seconds", "30", "--device", "2"])
    printed = capsys.readouterr().out
    assert code == 0
    assert "3..." in printed and "2..." in printed and "1..." in printed
    assert fake_sounddevice.calls[0]["device"] == 2


def test_record_ref_noisy_warns_exit_4_but_saves(fake_sounddevice, tmp_path, capsys):
    fake_sounddevice.config["noise_db"] = -22.0
    out = tmp_path / "noisy.wav"
    code = run(["record-ref", "--out", str(out), "--seconds", "40", "--no-countdown"])
    assert code == 4
    assert out.is_file()
    side = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert oa.WARN_NOISY in side["warnings"] and side["exit_code"] == 4
    assert 0 <= side["snr_db"] < 15
    printed = capsys.readouterr().out
    assert "record-ref: WARN" in printed and "noisy" in printed and "voice-check" in printed


def test_record_ref_too_short_warns(fake_sounddevice, tmp_path):
    fake_sounddevice.config["speech_seconds"] = 10
    out = tmp_path / "short.wav"
    assert run(["record-ref", "--out", str(out), "--seconds", "40", "--no-countdown"]) == 4
    side = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert side["warnings"] == [oa.WARN_SHORT]
    assert side["speech_seconds"] < 20
    assert 9 < side["duration"] < 12          # trailing 28 s of silence trimmed away


def test_record_ref_clipped_warns(fake_sounddevice, tmp_path):
    fake_sounddevice.config["speech_db"] = 6.0     # the fake ADC clips at full scale
    out = tmp_path / "hot.wav"
    assert run(["record-ref", "--out", str(out), "--seconds", "40", "--no-countdown"]) == 4
    side = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert oa.WARN_CLIPPED in side["warnings"]
    assert side["clipped"] is True and side["clipped_pct"] > 0.5
    assert side["peak_dbfs"] == pytest.approx(-3.0, abs=0.1)     # still normalised on output


def test_record_ref_silent_capture_warns_with_a_targeted_hint(fake_sounddevice, tmp_path, monkeypatch, capsys):
    """Nothing reached the ADC (muted input, wrong device, denied mic permission): say so instead of
    'find a quieter room'."""
    fake_sounddevice.config["noise_db"] = -130.0
    fake_sounddevice.config["speech_db"] = -100.0
    monkeypatch.setattr(oa.platform, "system", lambda: "Darwin")
    out = tmp_path / "quiet.wav"
    assert run(["record-ref", "--out", str(out), "--seconds", "40", "--no-countdown"]) == 4
    side = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert side["warnings"][0] == oa.WARN_SILENT and side["raw_peak_dbfs"] < oa.SILENT_PEAK_DB
    printed = capsys.readouterr().out
    assert "no signal" in printed and "--device" in printed and "Privacy & Security > Microphone" in printed
    assert "quieter room" not in printed
    assert oa.warnings_for({"raw_peak_dbfs": -20.0, "speech_seconds": 30, "snr_db": 30}) == []


def test_record_ref_retries_at_the_device_native_rate(fake_sounddevice, tmp_path):
    """A 44.1 kHz-only microphone (CoreAudio does not resample for PortAudio): retry once at its
    default_samplerate and resample, instead of dropping straight to ffmpeg."""
    fake_sounddevice.config["rate_ok"] = 44100
    out = tmp_path / "usb.wav"
    assert run(["record-ref", "--out", str(out), "--seconds", "30", "--device", "2", "--no-countdown"]) == 0
    assert [c["samplerate"] for c in fake_sounddevice.calls] == [SR, 44100]
    assert all(c["device"] == 2 for c in fake_sounddevice.calls)
    side = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert side["backend"] == "sounddevice" and side["native_sample_rate"] == 44100 and side["sample_rate"] == SR
    assert sf.info(str(out)).samplerate == SR
    assert 27 <= side["duration"] <= 29


def test_record_ref_list_devices(fake_sounddevice, capsys):
    assert run(["record-ref", "--list-devices"]) == 0
    out = capsys.readouterr().out
    assert "[0] * Built-in Microphone (2 ch)" in out
    assert "[2]   USB Microphone (1 ch)" in out
    assert "Built-in Output" not in out


def test_record_ref_bad_input(fake_sounddevice, tmp_path, capsys):
    assert run(["record-ref"]) == 4
    assert "--out" in capsys.readouterr().err
    assert run(["record-ref", "--out", str(tmp_path / "x.mp3")]) == 4
    assert run(["record-ref", "--out", str(tmp_path / "x.wav"), "--seconds", "0"]) == 4
    assert not list(tmp_path.iterdir())


def test_record_ref_backend_sounddevice_failure_is_exit_3(fake_sounddevice, tmp_path, capsys):
    fake_sounddevice.config["fail"] = True
    out = tmp_path / "v.wav"
    code = run(["record-ref", "--out", str(out), "--backend", "sounddevice", "--no-countdown"])
    assert code == 3
    assert "Invalid device" in capsys.readouterr().err
    assert not out.exists()


def test_record_ref_keyboard_interrupt_is_130(fake_sounddevice, tmp_path, monkeypatch, capsys):
    def boom(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(fake_sounddevice, "rec", boom)
    assert run(["record-ref", "--out", str(tmp_path / "v.wav"), "--no-countdown"]) == 130
    assert "interrupted" in capsys.readouterr().err


# --------------------------------------------------------------------------- ffmpeg fallback


def test_ffmpeg_record_args_per_platform(tmp_path):
    out = tmp_path / "raw.wav"
    win = oa.ffmpeg_record_args("ffmpeg.exe", "dshow", "Microphone (Realtek)", 60, out)
    assert win[:6] == ["ffmpeg.exe", "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
    assert win[6:10] == ["-f", "dshow", "-i", "audio=Microphone (Realtek)"]
    assert win[10:] == ["-t", "60", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(out)]
    mac = oa.ffmpeg_record_args("ffmpeg", "avfoundation", None, 30.5, out)
    assert mac[6:10] == ["-f", "avfoundation", "-i", ":default"]      # the system default input, not device 0
    assert "-t" in mac and mac[mac.index("-t") + 1] == "30.5"
    mac2 = oa.ffmpeg_record_args("ffmpeg", "avfoundation", "1", 30, out)
    assert mac2[9] == ":1"
    pulse = oa.ffmpeg_record_args("ffmpeg", "pulse", None, 60, out)
    assert pulse[6:10] == ["-f", "pulse", "-i", "default"]
    alsa = oa.ffmpeg_record_args("ffmpeg", "alsa", "hw:1,0", 60, out)
    assert alsa[6:10] == ["-f", "alsa", "-i", "hw:1,0"]
    for argv in (win, mac, pulse, alsa):
        assert all(isinstance(a, str) for a in argv) and argv[-1] == str(out)
    with pytest.raises(ValueError):
        oa.ffmpeg_record_args("ffmpeg", "dshow", None, 60, out)
    with pytest.raises(ValueError):
        oa.ffmpeg_record_args("ffmpeg", "jack", None, 60, out)


def test_ffmpeg_candidates(monkeypatch):
    assert oa.ffmpeg_candidates("Linux") == [("pulse", None), ("alsa", None)]
    assert oa.ffmpeg_candidates("Linux", "hw:0") == [("pulse", "hw:0"), ("alsa", "hw:0")]
    assert oa.ffmpeg_candidates("Darwin") == [("avfoundation", None)]
    assert oa.ffmpeg_candidates("Windows", "My Mic") == [("dshow", "My Mic")]
    monkeypatch.setattr(oa, "_run_capture", lambda argv, timeout=20: (
        '[dshow @ 0] DirectShow video devices\n[dshow @ 0]  "Cam"\n'
        '[dshow @ 0] DirectShow audio devices\n[dshow @ 0]  "Mic A"\n[dshow @ 0]  "Mic B"\n'))
    assert oa.ffmpeg_candidates("Windows", None, "ffmpeg") == [("dshow", "Mic A")]
    assert oa.ffmpeg_candidates("Windows", None, None) == []


def _fake_ffmpeg_writer(seconds_ok: bool = True, sr: int = SR):
    """A stand-in for subprocess.run that writes the WAV ffmpeg would have produced."""
    seen: list[list[str]] = []

    def runner(argv, timeout):
        seen.append(list(argv))
        fmt = argv[argv.index("-f") + 1]
        if fmt == "pulse":       # pretend PulseAudio is absent so the alsa candidate is used
            return subprocess.CompletedProcess(argv, 1, "", "pulse: Connection refused")
        out = Path(argv[-1])
        secs = float(argv[argv.index("-t") + 1])
        data = fake_sd.synth_recording(secs, sr)
        sf.write(str(out), data, sr, subtype="PCM_16")
        return subprocess.CompletedProcess(argv, 0, "", "")

    runner.seen = seen   # type: ignore[attr-defined]
    return runner


def test_record_ref_falls_back_to_ffmpeg(no_sounddevice, tmp_path, monkeypatch, capsys):
    runner = _fake_ffmpeg_writer()
    monkeypatch.setattr(oa, "_run_record", runner)
    monkeypatch.setattr(oa, "_find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(oa.platform, "system", lambda: "Linux")
    out = tmp_path / "voices" / "ff.wav"
    code = run(["record-ref", "--out", str(out), "--seconds", "30"])
    printed = capsys.readouterr().out
    assert code == 0, printed
    assert "falling back to ffmpeg" in printed
    # the backend is settled before the passage and the countdown, not after "speak now"
    assert printed.index("falling back to ffmpeg") < printed.index("part 1/3") < printed.index("3...")
    assert printed.index("3...") < printed.index("speak now")
    assert "pip install sounddevice" in printed          # ImportError -> the package is missing
    fmts = [a[a.index("-f") + 1] for a in runner.seen]
    assert fmts == ["pulse", "alsa"]
    assert runner.seen[-1][-1] == str(out.with_name("ff.raw.wav"))
    assert not out.with_name("ff.raw.wav").exists()          # temp capture removed
    side = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert side["backend"] == "ffmpeg (alsa:default)"
    assert side["exit_code"] == 0 and side["sample_rate"] == SR
    info = sf.info(str(out))
    assert info.samplerate == SR and info.subtype == "PCM_16"


def test_record_ref_ffmpeg_backend_explicit_resamples(fake_sounddevice, tmp_path, monkeypatch):
    runner = _fake_ffmpeg_writer(sr=44100)     # ffmpeg ignored -ar: still lands at 48 kHz
    monkeypatch.setattr(oa, "_run_record", runner)
    monkeypatch.setattr(oa, "_find_ffmpeg", lambda: "ffmpeg")
    out = tmp_path / "ff.wav"
    code = run(["record-ref", "--out", str(out), "--seconds", "30", "--backend", "ffmpeg",
                "--no-countdown", "--device", "hw:1"])
    assert code == 0
    assert fake_sounddevice.calls == []        # sounddevice never touched
    assert runner.seen[-1][runner.seen[-1].index("-i") + 1] == "hw:1"
    assert sf.info(str(out)).samplerate == SR


def test_numeric_device_is_not_forwarded_to_ffmpeg(fake_sounddevice, tmp_path, monkeypatch, capsys):
    """--device 2 is a PortAudio index; pulse would read it as a source index, avfoundation as its
    own device 2 and dshow cannot open 'audio=2': the fallback records from the OS default."""
    fake_sounddevice.config["fail"] = True
    runner = _fake_ffmpeg_writer()
    monkeypatch.setattr(oa, "_run_record", runner)
    monkeypatch.setattr(oa, "_find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(oa.platform, "system", lambda: "Linux")
    out = tmp_path / "v.wav"
    assert run(["record-ref", "--out", str(out), "--seconds", "30", "--device", "2", "--no-countdown"]) == 0
    printed = capsys.readouterr().out
    assert [a[a.index("-i") + 1] for a in runner.seen] == ["default", "default"]
    assert "sounddevice failed" in printed and "start reading again" in printed
    assert "is a sounddevice index" in printed and "OS default input" in printed
    side = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert side["backend"] == "ffmpeg (alsa:default)" and side["device"] == "2"


def test_portaudio_library_missing_is_named(monkeypatch):
    real = oa.importlib.import_module

    def missing_lib(name, *a, **k):
        if name == "sounddevice":
            raise OSError("PortAudio library not found")
        return real(name, *a, **k)

    monkeypatch.setattr(oa.importlib, "import_module", missing_lib)
    mod, note = oa._import_sounddevice_detail()
    assert mod is None and "libportaudio2" in note and "pip install" not in note
    assert "libportaudio2" in oa.list_input_devices()["note"]
    with pytest.raises(oa.RecordError, match="sounddevice not importable: .*libportaudio2"):
        oa.record_sounddevice(1)


def test_ffmpeg_error_summary_prefers_the_informative_line():
    pulse = ("[in#0] Unknown input format: 'pulse'\nError opening input file default.\n"
             "Error opening input files: Invalid argument\n")
    assert oa.ffmpeg_error_summary(pulse, 234) == "[in#0] Unknown input format: 'pulse'"
    assert oa.ffmpeg_error_summary("[alsa] cannot open audio device default (No such file)", 1) == \
        "[alsa] cannot open audio device default (No such file)"
    assert oa.ffmpeg_error_summary("Error opening input files: Invalid argument", 1) == \
        "Error opening input files: Invalid argument"
    assert oa.ffmpeg_error_summary("", 7) == "exit 7"


def test_ffmpeg_output_is_decoded_as_utf8(monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(oa.subprocess, "run", fake_run)
    oa._run_capture(["ffmpeg"])
    assert seen["encoding"] == "utf-8" and seen["errors"] == "replace"
    oa._run_record(["ffmpeg"], timeout=5)
    assert seen["encoding"] == "utf-8"


def test_record_ffmpeg_removes_the_partial_capture_on_timeout(monkeypatch, tmp_path):
    def timing_out(argv, timeout):
        Path(argv[-1]).write_bytes(b"RIFF" + b"\0" * 100)     # ffmpeg had opened the file already
        raise subprocess.TimeoutExpired(argv, timeout)

    monkeypatch.setattr(oa, "_run_record", timing_out)
    with pytest.raises(oa.RecordError, match="timed out"):
        oa.record_ffmpeg(5, tmp_path / "v.wav", None, "Linux", "ffmpeg")
    assert not (tmp_path / "v.raw.wav").exists()


def test_record_ref_no_backend_is_exit_3(no_sounddevice, tmp_path, monkeypatch, capsys):
    def failing(argv, timeout):
        return subprocess.CompletedProcess(argv, 1, "", "[alsa] cannot open audio device default (No such file)")

    monkeypatch.setattr(oa, "_run_record", failing)
    monkeypatch.setattr(oa, "_find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(oa.platform, "system", lambda: "Linux")
    out = tmp_path / "v.wav"
    assert run(["record-ref", "--out", str(out), "--no-countdown"]) == 3
    err = capsys.readouterr().err
    assert "sounddevice not importable" in err and "cannot open audio device" in err
    assert not out.exists() and not out.with_suffix(".json").exists()


def test_record_ref_no_ffmpeg_binary_is_exit_3(no_sounddevice, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(oa, "_find_ffmpeg", lambda: None)
    assert run(["record-ref", "--out", str(tmp_path / "v.wav"), "--no-countdown"]) == 3
    assert "ffmpeg not found" in capsys.readouterr().err


def test_record_ffmpeg_windows_without_dshow_device(monkeypatch, tmp_path):
    monkeypatch.setattr(oa, "_run_capture", lambda argv, timeout=20: "[dshow @ 0] DirectShow audio devices\n")
    with pytest.raises(oa.RecordError, match="no dshow audio device"):
        oa.record_ffmpeg(5, tmp_path / "v.wav", None, "Windows", "ffmpeg.exe")


def test_record_ffmpeg_oserror_is_reported(monkeypatch, tmp_path):
    def raising(argv, timeout):
        raise OSError("exec format error")

    monkeypatch.setattr(oa, "_run_record", raising)
    with pytest.raises(oa.RecordError, match="exec format error"):
        oa.record_ffmpeg(5, tmp_path / "v.wav", None, "Darwin", "ffmpeg")


# --------------------------------------------------------------------------- register


def test_register_wires_both_commands():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd")
    run_map: dict = {}
    oa.register(sub, run_map)
    assert set(run_map) == {"record-ref", "devices"}
    a = p.parse_args(["record-ref", "--out", "voices/x.wav", "--seconds", "45", "--device", "1",
                      "--backend", "ffmpeg", "--no-countdown"])
    assert (a.out, a.seconds, a.device, a.backend, a.no_countdown) == ("voices/x.wav", 45.0, "1", "ffmpeg", True)
    assert a.fn is oa.cmd_record_ref and run_map["record-ref"] is oa.cmd_record_ref
    d = p.parse_args(["devices"])
    assert d.out == "demo-output" and run_map["devices"] is oa.cmd_devices
