"""Chrome launch/shutdown edge cases: a launcher that detaches (Chrome 15x / Edge on
Windows exit 0 immediately while DevTools comes up on the port) and killing the
real browser process on close."""
from __future__ import annotations

import http.server
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from demo_smoke import chrome

KIT = Path(__file__).resolve().parents[1]
CHROME_DEFAULT = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"


class _FakeProc:
    """Stands in for the launcher ``Popen``: ``poll()`` returns a fixed code."""

    def __init__(self, code):
        self._code = code
        self.pid = 4242
        self.returncode = code

    def poll(self):
        return self._code


class _VersionHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"Browser": "FakeChrome/152", "webSocketDebuggerUrl": "ws://x"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence
        return


def _serve_version_later(port: int, delay_s: float):
    """Start answering /json/version on ``port`` after ``delay_s`` (in a thread)."""
    state = {"server": None}

    def run():
        time.sleep(delay_s)
        srv = http.server.HTTPServer(("127.0.0.1", port), _VersionHandler)
        state["server"] = srv
        srv.serve_forever()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return state


def test_wait_tolerates_launcher_that_exits_zero(tmp_path):
    port = chrome._free_port()
    state = _serve_version_later(port, 0.6)
    try:
        version = chrome._wait_for_devtools(port, _FakeProc(0), tmp_path / "chrome.log", timeout=10.0)
    finally:
        if state["server"] is not None:
            state["server"].shutdown()
    assert version["Browser"] == "FakeChrome/152"


def test_wait_raises_on_nonzero_exit(tmp_path):
    (tmp_path / "chrome.log").write_bytes(b"boom: no display\n")
    with pytest.raises(chrome.ChromeError, match="exited with code 1"):
        chrome._wait_for_devtools(chrome._free_port(), _FakeProc(1), tmp_path / "chrome.log", timeout=5.0)


def test_wait_detached_without_endpoint_says_so(tmp_path):
    with pytest.raises(chrome.ChromeError, match="launcher process exited 0"):
        chrome._wait_for_devtools(chrome._free_port(), _FakeProc(0), tmp_path / "chrome.log", timeout=0.7)


def test_pid_alive_for_self_and_for_a_finished_child():
    assert chrome._pid_alive(os.getpid())
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=30)
    assert not chrome._pid_alive(proc.pid)


def _need_chrome():
    if "DEMO_SMOKE_CHROME" not in os.environ and Path(CHROME_DEFAULT).exists():
        os.environ["DEMO_SMOKE_CHROME"] = CHROME_DEFAULT
    if not chrome.find_chrome():
        pytest.skip("no Chrome/Chromium available")


def test_close_ends_the_real_browser_process(tmp_path):
    _need_chrome()
    session = chrome.launch(tmp_path, {"width": 800, "height": 600}, headless=True)
    try:
        pid = session.browser_pid
        assert isinstance(pid, int) and pid > 0
        assert chrome._pid_alive(pid)
        assert session.pid == pid
    finally:
        session.close()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and chrome._pid_alive(pid):
        time.sleep(0.1)
    assert not chrome._pid_alive(pid)
    assert not (tmp_path / "chrome-profile").exists()


def test_launch_passes_an_absolute_profile_dir_for_a_relative_out(tmp_path, monkeypatch):
    """Chrome 152 on Windows exits 0 when --user-data-dir is relative (seen on the tablet)."""
    seen = {}

    def fake_popen(args, **kw):
        seen["args"] = args
        raise OSError("stop here")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(chrome.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(chrome, "find_chrome", lambda: sys.executable)
    with pytest.raises(chrome.ChromeError, match="could not start Chrome"):
        chrome.launch(Path("demo-output") / "fixture", {}, headless=True)
    udd = next(a for a in seen["args"] if a.startswith("--user-data-dir=")).split("=", 1)[1]
    assert Path(udd).is_absolute()
    assert Path(udd).parent == (tmp_path / "demo-output" / "fixture").resolve()
