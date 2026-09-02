"""devices: sounddevice inputs (fake module / missing), screens per OS, never crashes."""

from __future__ import annotations

import argparse
import json
import sys

import pytest

from demo_smoke import onboard_audio as oa
from tests.fakes import sounddevice as fake_sd


@pytest.fixture
def fake_sounddevice(monkeypatch):
    fake_sd.reset()
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    yield fake_sd
    fake_sd.reset()


@pytest.fixture
def no_sounddevice(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", None)


def run_devices(out: str | None) -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd")
    run_map: dict = {}
    oa.register(sub, run_map)
    argv = ["devices"] + (["--out", out] if out else [])
    return run_map["devices"](p.parse_args(argv))


AVF = """[AVFoundation indev @ 0x7f] AVFoundation video devices:
[AVFoundation indev @ 0x7f] [0] FaceTime HD Camera
[AVFoundation indev @ 0x7f] [1] Capture screen 0
[AVFoundation indev @ 0x7f] [2] Capture screen 1
[AVFoundation indev @ 0x7f] AVFoundation audio devices:
[AVFoundation indev @ 0x7f] [0] MacBook Pro Microphone
[AVFoundation indev @ 0x7f] [1] Scarlett 2i2 USB
: Input/output error
"""

DSHOW_OLD = """[dshow @ 000001] DirectShow video devices (some may be both video and audio devices)
[dshow @ 000001]  "Integrated Camera"
[dshow @ 000001]     Alternative name "@device_pnp_\\\\?\\usb#vid_04f2"
[dshow @ 000001]  "screen-capture-recorder"
[dshow @ 000001] DirectShow audio devices
[dshow @ 000001]  "Microphone (Realtek(R) Audio)"
[dshow @ 000001]     Alternative name "@device_cm_{33D9A762}\\wave_{5E6}"
[dshow @ 000001]  "Headset Microphone (Jabra)"
dummy: Immediate exit requested
"""

DSHOW_NEW = """[dshow @ 000001] "Integrated Camera" (video)
[dshow @ 000001]   Alternative name "@device_pnp_\\\\?\\usb#vid_04f2"
[dshow @ 000001] "Microphone (Realtek(R) Audio)" (audio)
[dshow @ 000001]   Alternative name "@device_cm_{33D9A762}\\wave_{5E6}"
[dshow @ 000001] "Screen Capture (OBS Virtual)" (video)
dummy: Immediate exit requested
"""


# --------------------------------------------------------------------------- parsers


def test_parse_avfoundation_devices():
    d = oa.parse_avfoundation_devices(AVF)
    assert d["video"] == [(0, "FaceTime HD Camera"), (1, "Capture screen 0"), (2, "Capture screen 1")]
    assert d["audio"] == [(0, "MacBook Pro Microphone"), (1, "Scarlett 2i2 USB")]
    assert oa.parse_avfoundation_devices("") == {"video": [], "audio": []}


def test_parse_dshow_devices_old_and_new_layouts():
    old = oa.parse_dshow_devices(DSHOW_OLD)
    assert old["video"] == ["Integrated Camera", "screen-capture-recorder"]
    assert old["audio"] == ["Microphone (Realtek(R) Audio)", "Headset Microphone (Jabra)"]
    new = oa.parse_dshow_devices(DSHOW_NEW)
    assert new["video"] == ["Integrated Camera", "Screen Capture (OBS Virtual)"]
    assert new["audio"] == ["Microphone (Realtek(R) Audio)"]
    assert oa.parse_dshow_devices("garbage\n") == {"video": [], "audio": []}


# --------------------------------------------------------------------------- audio inputs


def test_list_input_devices_with_fake(fake_sounddevice):
    res = oa.list_input_devices()
    assert res["available"] is True and res["note"] is None
    assert [(d["index"], d["name"], d["channels"], d["default"]) for d in res["devices"]] == [
        (0, "Built-in Microphone", 2, True), (2, "USB Microphone", 1, False)]
    fake_sounddevice.default.device = [2, 1]
    assert [d["default"] for d in oa.list_input_devices()["devices"]] == [False, True]
    fake_sounddevice.default.device = [-1, -1]
    assert not any(d["default"] for d in oa.list_input_devices()["devices"])


def test_list_input_devices_without_sounddevice(no_sounddevice):
    res = oa.list_input_devices()
    assert res["available"] is False and res["devices"] == []
    assert "sounddevice" in res["note"] and "ffmpeg" in res["note"]
    assert "pip install sounddevice" in res["note"] and "libportaudio2" not in res["note"]


def test_list_input_devices_labels_host_apis_when_there_are_several(fake_sounddevice, monkeypatch):
    """Windows lists each microphone under MME, DirectSound, WASAPI and WDM-KS: label the entries."""
    monkeypatch.setattr(fake_sounddevice, "query_hostapis",
                        lambda index=None: [{"name": "MME"}, {"name": "Windows WASAPI"}])
    devs = [{"name": "Microphone (Realtek(R) Audio", "max_input_channels": 2, "hostapi": 0},
            {"name": "Microphone (Realtek(R) Audio)", "max_input_channels": 2, "hostapi": 1},
            {"name": "Odd", "max_input_channels": 1, "hostapi": 9}]
    monkeypatch.setattr(fake_sounddevice, "query_devices", lambda device=None, kind=None: devs)
    names = [d["name"] for d in oa.list_input_devices()["devices"]]
    assert names == ["Microphone (Realtek(R) Audio [MME]", "Microphone (Realtek(R) Audio) [Windows WASAPI]", "Odd"]
    # one host API (Linux ALSA, macOS Core Audio): no label
    monkeypatch.setattr(fake_sounddevice, "query_hostapis", lambda index=None: [{"name": "ALSA"}])
    assert oa.list_input_devices()["devices"][0]["name"] == "Microphone (Realtek(R) Audio"


def test_list_input_devices_query_failure(fake_sounddevice, monkeypatch):
    def boom():
        raise fake_sounddevice.PortAudioError("PortAudio not initialized")

    monkeypatch.setattr(fake_sounddevice, "query_devices", boom)
    res = oa.list_input_devices()
    assert res["available"] is False and "PortAudio not initialized" in res["note"]


def test_list_input_devices_no_inputs(fake_sounddevice, monkeypatch):
    monkeypatch.setattr(fake_sounddevice, "query_devices",
                        lambda device=None, kind=None: [{"name": "Speakers", "max_input_channels": 0}])
    res = oa.list_input_devices()
    assert res["available"] is True and res["devices"] == [] and "no input devices" in res["note"]


# --------------------------------------------------------------------------- screens


def test_screens_linux_uses_display_only(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":1")
    calls = []
    monkeypatch.setattr(oa, "_run_capture", lambda argv, timeout=20: calls.append(argv) or "")
    res = oa.list_screens("Linux")
    assert res["available"] is True and res["screens"] == [{"index": 0, "name": "X display :1 (x11grab)"}]
    assert calls == []
    monkeypatch.delenv("DISPLAY", raising=False)
    res = oa.list_screens("Linux")
    assert res["available"] is False and res["screens"] == [] and "DISPLAY" in res["note"]


def test_screens_macos_parses_avfoundation(monkeypatch):
    seen = []

    def fake_run(argv, timeout=20):
        seen.append(argv)
        return AVF

    monkeypatch.setattr(oa, "_run_capture", fake_run)
    res = oa.list_screens("Darwin", ffmpeg="/usr/local/bin/ffmpeg")
    assert seen == [["/usr/local/bin/ffmpeg", "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""]]
    assert res["available"] is True
    assert res["screens"] == [{"index": 1, "name": "Capture screen 0"}, {"index": 2, "name": "Capture screen 1"}]
    monkeypatch.setattr(oa, "_run_capture", lambda argv, timeout=20: AVF.replace("Capture screen", "Cam"))
    res = oa.list_screens("Darwin", ffmpeg="ffmpeg")
    assert res["available"] is False and "Screen Recording" in res["note"]


def test_screens_windows_parses_dshow(monkeypatch):
    seen = []

    def fake_run(argv, timeout=20):
        seen.append(argv)
        return DSHOW_OLD

    monkeypatch.setattr(oa, "_run_capture", fake_run)
    res = oa.list_screens("Windows", ffmpeg="C:\\ffmpeg\\bin\\ffmpeg.exe")
    assert seen == [["C:\\ffmpeg\\bin\\ffmpeg.exe", "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"]]
    assert res["available"] is True
    assert res["screens"][0]["name"].startswith("desktop (gdigrab")
    assert res["screens"][1] == {"index": 1, "name": "screen-capture-recorder (dshow)"}


def test_screens_degrade_without_ffmpeg_or_on_errors(monkeypatch):
    monkeypatch.setattr(oa, "_find_ffmpeg", lambda: None)
    mac = oa.list_screens("Darwin")
    assert mac["available"] is False and "ffmpeg not found" in mac["note"]
    win = oa.list_screens("Windows")
    assert win["available"] is True and len(win["screens"]) == 1 and "ffmpeg not found" in win["note"]

    def raising(argv, timeout=20):
        raise OSError("permission denied")

    monkeypatch.setattr(oa, "_run_capture", raising)
    mac = oa.list_screens("Darwin", ffmpeg="ffmpeg")
    assert mac["available"] is False and "permission denied" in mac["note"]
    win = oa.list_screens("Windows", ffmpeg="ffmpeg")
    assert win["screens"] and "permission denied" in win["note"]
    other = oa.list_screens("FreeBSD", ffmpeg="ffmpeg")
    assert other["available"] is False and "FreeBSD" in other["note"]


# --------------------------------------------------------------------------- command


def test_devices_command_prints_and_logs(fake_sounddevice, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(oa.platform, "system", lambda: "Linux")
    out = tmp_path / "out"
    assert run_devices(str(out)) == 0
    printed = capsys.readouterr().out
    assert printed.startswith("devices: 2 audio input(s), 1 screen(s)")
    assert "[0] * Built-in Microphone (2 ch)" in printed
    assert "[2]   USB Microphone (1 ch)" in printed
    assert "[0] X display :0 (x11grab)" in printed
    log = json.loads((out / "logs" / "devices.json").read_text(encoding="utf-8"))
    assert log["audio"]["devices"][0]["name"] == "Built-in Microphone"
    assert log["screens"]["screens"][0]["index"] == 0


def test_devices_command_unavailable_everything_still_exit_0(no_sounddevice, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(oa.platform, "system", lambda: "Linux")
    assert run_devices(str(tmp_path / "o")) == 0
    printed = capsys.readouterr().out
    assert "devices: 0 audio input(s), 0 screen(s)" in printed
    assert "audio inputs (sounddevice):\n  unavailable:" in printed
    assert "screens (--capture screen):\n  unavailable:" in printed
    assert (tmp_path / "o" / "logs" / "devices.json").is_file()


def test_devices_command_never_crashes(fake_sounddevice, tmp_path, monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(oa, "list_input_devices", boom)
    monkeypatch.delenv("DEMO_SMOKE_DEBUG", raising=False)
    assert run_devices(str(tmp_path / "o")) == 3
    assert "devices: unexpected" in capsys.readouterr().err
