import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from demo_smoke import env

FFMPEG_I_SAMPLE = """ffmpeg version 7.0.2-static https://johnvansickle.com/ffmpeg/  Copyright (c) 2000-2024 the FFmpeg developers
Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'final/demo.mp4':
  Metadata:
    major_brand     : isom
  Duration: 00:01:23.45, start: 0.000000, bitrate: 1234 kb/s
  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), yuv420p(progressive), 1920x1080 [SAR 1:1 DAR 16:9], 1100 kb/s, 30 fps, 30 tbr, 15360 tbn (default)
  Stream #0:1[0x2](und): Audio: aac (LC) (mp4a / 0x6134706D), 48000 Hz, stereo, fltp, 128 kb/s (default)
At least one output file must be specified
"""


def test_paths_creates_layout(out_dir):
    p = env.Paths(out_dir)
    for d in (p.raw, p.audio, p.clips, p.final, p.logs):
        assert d.is_dir()
    assert p.logs == out_dir / "logs"
    assert os.fspath(p) == str(out_dir)
    assert "Paths(" in repr(p)


def test_find_ffmpeg_and_version():
    ff = env.find_ffmpeg()
    assert Path(ff).is_file()
    assert env.ffmpeg_version(ff)


def test_find_ffmpeg_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("DEMO_SMOKE_FFMPEG", str(tmp_path / "nope"))
    with pytest.raises(RuntimeError, match="DEMO_SMOKE_FFMPEG"):
        env.find_ffmpeg()
    fake = tmp_path / "ffmpeg"
    fake.write_text("")
    monkeypatch.setenv("DEMO_SMOKE_FFMPEG", str(fake))
    assert env.find_ffmpeg() == str(fake)


def test_find_ffmpeg_missing_has_install_hint(monkeypatch):
    monkeypatch.delenv("DEMO_SMOKE_FFMPEG", raising=False)
    monkeypatch.setattr(env.shutil, "which", lambda name: None)
    monkeypatch.setitem(__import__("sys").modules, "imageio_ffmpeg", None)
    with pytest.raises(RuntimeError, match="ffmpeg not found"):
        env.find_ffmpeg()


def test_find_chrome(monkeypatch, tmp_path):
    monkeypatch.setenv("DEMO_SMOKE_CHROME", str(tmp_path / "nochrome"))
    assert env.find_chrome() is None
    monkeypatch.delenv("DEMO_SMOKE_CHROME")
    found = env.find_chrome()
    assert found is None or Path(found).is_file()


def test_find_chrome_playwright_layouts(monkeypatch, tmp_path):
    """Both the legacy chrome-linux layout and Playwright's Chrome-for-Testing layouts are found."""
    monkeypatch.delenv("DEMO_SMOKE_CHROME", raising=False)
    monkeypatch.setattr(env, "_CHROME_CANDIDATES", {})
    monkeypatch.setattr(env.shutil, "which", lambda name: None)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    pats = env.playwright_chrome_patterns()
    assert any(p.endswith("chromium-*/chrome-linux64/chrome") for p in pats)
    assert any(p.endswith("chromium-*/chrome-win64/chrome.exe") for p in pats)
    assert any("Google Chrome for Testing.app" in p for p in pats)
    assert env.find_chrome() is None or env.find_chrome().startswith("/opt/pw-browsers")
    cft = tmp_path / ".cache" / "ms-playwright" / "chromium-1234" / "chrome-linux64" / "chrome"
    cft.parent.mkdir(parents=True)
    cft.write_text("")
    found = env.find_chrome()
    assert found == str(cft) or found.startswith("/opt/pw-browsers")   # /opt/pw-browsers ranks first


def test_find_ffprobe_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DEMO_SMOKE_FFPROBE", str(tmp_path / "nope"))
    assert env.find_ffprobe() is None


def test_torch_device_value():
    assert env.torch_device() in ("cuda", "rocm", "mps", "cpu", "none")


def test_parse_ffmpeg_i():
    info = env.parse_ffmpeg_i(FFMPEG_I_SAMPLE)
    assert info == {"duration": pytest.approx(83.45), "width": 1920, "height": 1080,
                    "has_audio": True, "audio_duration": pytest.approx(83.45)}
    only_audio = "  Duration: 00:00:02.50, start: 0\n  Stream #0:0: Audio: pcm_s16le, 24000 Hz, mono\n"
    assert env.parse_ffmpeg_i(only_audio) == {"duration": 2.5, "width": 0, "height": 0,
                                              "has_audio": True, "audio_duration": 2.5}
    assert env.parse_ffmpeg_i("garbage")["duration"] == 0.0


@pytest.fixture(scope="module")
def synthetic_media(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    ff = env.find_ffmpeg()
    mp4 = d / "clip.mp4"
    subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=30",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(mp4)], check=True)
    wav = d / "tone.wav"
    subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1.5",
                    "-ar", "24000", str(wav)], check=True)
    silent = d / "silent.mp4"
    subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=1:size=64x64:rate=10",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(silent)], check=True)
    return {"mp4": mp4, "wav": wav, "silent": silent}


def test_media_info_ffmpeg_fallback(synthetic_media, monkeypatch):
    monkeypatch.setattr(env, "find_ffprobe", lambda: None)
    info = env.media_info(synthetic_media["mp4"])
    assert info["width"] == 320 and info["height"] == 240
    assert info["has_audio"] is True
    assert info["duration"] == pytest.approx(2.0, abs=0.15)
    assert info["audio_duration"] == pytest.approx(2.0, abs=0.15)
    info = env.media_info(synthetic_media["wav"])
    assert info["width"] == 0 and info["has_audio"] is True
    assert info["duration"] == pytest.approx(1.5, abs=0.05)
    info = env.media_info(synthetic_media["silent"])
    assert info["has_audio"] is False and info["audio_duration"] is None
    assert info["width"] == 64


def test_media_info_prefers_ffprobe(synthetic_media, monkeypatch, tmp_path):
    fake = tmp_path / "ffprobe.py"
    fake.write_text("")
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)

        class CP:
            returncode = 0
            stdout = json.dumps({"format": {"duration": "9.5"},
                                 "streams": [{"codec_type": "video", "width": 100, "height": 50},
                                             {"codec_type": "audio", "duration": "9.4"}]})
            stderr = ""
        return CP()

    monkeypatch.setattr(env, "find_ffprobe", lambda: str(fake))
    monkeypatch.setattr(env, "_run", fake_run)
    info = env.media_info(synthetic_media["mp4"])
    assert info == {"duration": 9.5, "width": 100, "height": 50, "has_audio": True, "audio_duration": 9.4}
    assert calls[0][0] == str(fake) and "-show_streams" in calls[0]


def test_media_info_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="media file not found"):
        env.media_info(tmp_path / "nope.mp4")


def test_detect_never_crashes(monkeypatch, tmp_path, unreachable_url):
    monkeypatch.setenv("DEMO_SMOKE_CHROME", str(tmp_path / "nochrome"))
    monkeypatch.setenv("DEMO_SMOKE_FFMPEG", str(tmp_path / "noffmpeg"))
    rep = env.detect(unreachable_url, "some-model")
    assert rep["ffmpeg"] is None and rep["chrome"] is None
    assert rep["llm"]["reachable"] is False and rep["llm"]["tool_call"] is None
    assert any("DEMO_SMOKE_FFMPEG" in h for h in rep["hints"])
    assert any("DEMO_SMOKE_CHROME points to a missing file" in h for h in rep["hints"])
    assert any("not reachable" in h for h in rep["hints"])
    assert set(rep["hf_weights"]) == {"turbo", "nano", "classic"}
    assert rep["tts_ready"] is False
    json.dumps(rep)  # serializable
    monkeypatch.delenv("DEMO_SMOKE_CHROME")
    monkeypatch.setattr(env, "find_chrome", lambda: None)
    rep = env.detect()
    assert any(h.startswith("Chrome/Chromium not found") and "playwright install" not in h for h in rep["hints"])


def test_hf_cache_dir_precedence(monkeypatch, tmp_path):
    for k in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "HF_HOME", "XDG_CACHE_HOME"):
        monkeypatch.delenv(k, raising=False)
    assert env.hf_cache_dir() == str(Path.home() / ".cache" / "huggingface" / "hub")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))              # like huggingface_hub
    assert env.hf_cache_dir() == str(tmp_path / "xdg" / "huggingface" / "hub")
    monkeypatch.setenv("MYBASE", str(tmp_path))
    monkeypatch.setenv("HF_HOME", "$MYBASE/expanded")                         # $VAR expanded
    assert env.hf_cache_dir() == str(tmp_path / "expanded" / "hub")
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    assert env.hf_cache_dir() == str(tmp_path / "hf" / "hub")
    monkeypatch.setenv("HUGGINGFACE_HUB_CACHE", str(tmp_path / "legacy"))
    assert env.hf_cache_dir() == str(tmp_path / "legacy")
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    assert env.hf_cache_dir() == str(tmp_path / "hub")


def fill_snapshot(cache: Path, backend: str, commit: str = "abc", skip: str | None = None) -> Path:
    """A huggingface_hub-shaped cache entry for ``backend`` with every weight file (minus ``skip``)."""
    repo = cache / ("models--" + env.HF_REPOS[backend].replace("/", "--"))
    (repo / "refs").mkdir(parents=True, exist_ok=True)
    (repo / "refs" / "main").write_text(commit + "\n")
    snap = repo / "snapshots" / commit
    snap.mkdir(parents=True, exist_ok=True)
    (repo / "blobs").mkdir(exist_ok=True)
    for f in env.HF_WEIGHT_FILES[backend]:
        if f != skip and not (snap / f).is_symlink():
            blob = repo / "blobs" / f"sha-{f}"
            blob.write_bytes(b"x")
            (snap / f).symlink_to(blob)         # snapshots hold symlinks into blobs/
    return repo


def test_hf_weights_present(tmp_path):
    cache = tmp_path / "hub"
    assert env.hf_weights_present(str(cache)) == {"turbo": False, "nano": False, "classic": False}
    repo = cache / "models--ResembleAI--chatterbox-turbo"
    (repo / "refs").mkdir(parents=True)
    (repo / "refs" / "main").write_text("abc")
    assert env.hf_weights_present(str(cache))["turbo"] is False      # ref without a snapshot
    (repo / "snapshots" / "abc").mkdir(parents=True)
    assert env.hf_weights_present(str(cache))["turbo"] is False      # interrupted prefetch: empty snapshot dir
    fill_snapshot(cache, "turbo", skip="s3gen_meanflow.safetensors")
    assert env.hf_weights_present(str(cache))["turbo"] is False      # one weight file still missing
    fill_snapshot(cache, "turbo")
    assert env.hf_weights_present(str(cache)) == {"turbo": True, "nano": False, "classic": False}
    (repo / "blobs" / "deadbeef.incomplete").write_bytes(b"")
    assert env.hf_weights_present(str(cache))["turbo"] is False      # a download is still in flight
    (repo / "blobs" / "deadbeef.incomplete").unlink()
    fill_snapshot(cache, "nano")
    fill_snapshot(cache, "classic")
    assert env.hf_weights_present(str(cache)) == {"turbo": True, "nano": True, "classic": True}
    # the file lists are what chatterbox's from_local actually loads
    assert "t3_nano_v1.safetensors" in env.HF_WEIGHT_FILES["nano"]
    assert "conds.pt" in env.HF_WEIGHT_FILES["classic"]


def test_chatterbox_nano_supported_scans_source(tmp_path, monkeypatch):
    import importlib
    import sys

    site = tmp_path / "site"
    (site / "chatterbox").mkdir(parents=True)
    # the real __init__ imports huggingface_hub (which freezes HF_HUB_OFFLINE): the probe must not run it
    (site / "chatterbox" / "__init__.py").write_text("raise RuntimeError('chatterbox/__init__.py was imported')\n")
    turbo = site / "chatterbox" / "tts_turbo.py"
    turbo.write_text("class ChatterboxTurboTTS:\n    @classmethod\n    def from_pretrained(cls, device):\n        pass\n")
    for name in [m for m in sys.modules if m == "chatterbox" or m.startswith("chatterbox.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.syspath_prepend(str(site))
    importlib.invalidate_caches()
    assert env.chatterbox_importable() is True
    assert env.chatterbox_nano_supported() is False                  # PyPI 0.1.7 shape
    turbo.write_text("NANO_REPO_ID = 'ResembleAI/chatterbox-nano'\nclass ChatterboxTurboTTS:\n"
                     "    @classmethod\n    def from_pretrained(cls, device, nano=False):\n        pass\n")
    assert env.chatterbox_nano_supported() is True                   # git master shape
    assert "chatterbox" not in sys.modules
    from demo_smoke import tts
    monkeypatch.setattr(env, "torch_device", lambda: "cpu")
    assert tts.resolve_backend("auto") == "nano"                     # doctor's tts_auto probe: import-free too
    assert "chatterbox" not in sys.modules
    turbo.unlink()
    assert env.chatterbox_nano_supported() is False                  # package without tts_turbo.py


def test_detect_reports_tts_auto_and_readiness(tmp_path, monkeypatch):
    monkeypatch.setattr(env, "torch_device", lambda: "cpu")
    monkeypatch.setattr(env, "chatterbox_importable", lambda: True)
    monkeypatch.setattr(env, "chatterbox_nano_supported", lambda: False)
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    rep = env.detect()
    assert rep["tts_auto"] == "turbo" and rep["tts_ready"] is False
    assert any("prefetch --tts turbo" in h for h in rep["hints"])
    fill_snapshot(tmp_path / "hub", "turbo", commit="x")
    rep = env.detect()
    assert rep["tts_ready"] is True and not any("prefetch" in h for h in rep["hints"])
    assert "opencode" in rep


def test_detect_with_fake_llm(fake_llm):
    fake_llm.queue.append({"tool_calls": [{"name": "get_step_status", "arguments": {"step_id": "open"}}]})
    rep = env.detect(fake_llm.base_url, "fake-model")
    assert rep["ffmpeg"] and rep["llm"]["reachable"] is True
    assert rep["llm"]["tool_call"]["pass"] is True
    assert rep["torch_device"] == env.torch_device()
    assert isinstance(rep["chatterbox"], bool)


# --------------------------------------------------------------------------- Smart App Control


class _FakeWinreg:
    """Just enough of ``winreg`` for ``smart_app_control``: one key, one value (or none)."""

    HKEY_LOCAL_MACHINE = object()

    def __init__(self, value=None, key_exists=True):
        self.value = value
        self.key_exists = key_exists
        self.opened: list[str] = []

    def OpenKey(self, root, path):  # winreg's real (CamelCase) name
        assert root is self.HKEY_LOCAL_MACHINE
        self.opened.append(path)
        if not self.key_exists:
            raise FileNotFoundError(2, "key not found")

        class _Key:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

        return _Key()

    def QueryValueEx(self, key, name):  # winreg's real (CamelCase) name
        assert name == env.SAC_VALUE
        if self.value is None:
            raise FileNotFoundError(2, "value not found")
        return self.value, 4   # REG_DWORD


@pytest.mark.parametrize("value, expected", [(1, "on"), (2, "evaluation"), (0, "off"), (7, "unknown"),
                                             ("garbage", "unknown"), (None, "unknown")])
def test_smart_app_control_states(value, expected):
    fake = _FakeWinreg(value)
    assert env.smart_app_control(fake) == expected
    assert fake.opened == [env.SAC_KEY]
    assert env.SAC_KEY == r"SYSTEM\CurrentControlSet\Control\CI\Policy"
    assert env.SAC_VALUE == "VerifiedAndReputablePolicyState"


def test_smart_app_control_missing_key_and_non_windows(monkeypatch):
    assert env.smart_app_control(_FakeWinreg(key_exists=False)) == "unknown"
    monkeypatch.setattr(env, "_is_windows", lambda: False)
    assert env.smart_app_control() is None        # not applicable: no registry read at all


def test_detect_reports_smart_app_control_only_on_windows(monkeypatch, tmp_path):
    monkeypatch.setenv("DEMO_SMOKE_CHROME", str(tmp_path / "nochrome"))
    monkeypatch.setattr(env, "_is_windows", lambda: False)
    rep = env.detect()
    assert rep["smart_app_control"] is None
    assert not any("Smart App Control" in h for h in rep["hints"])

    monkeypatch.setattr(env, "_is_windows", lambda: True)
    monkeypatch.setitem(sys.modules, "winreg", _FakeWinreg(1))
    rep = env.detect()
    assert rep["smart_app_control"] == "on"
    assert env.SAC_HINT in rep["hints"]
    assert "Windows Security > App & browser control" in env.SAC_HINT and "WSL2" in env.SAC_HINT
    json.dumps(rep)

    monkeypatch.setitem(sys.modules, "winreg", _FakeWinreg(0))
    rep = env.detect()
    assert rep["smart_app_control"] == "off"
    assert not any("Smart App Control" in h for h in rep["hints"])
