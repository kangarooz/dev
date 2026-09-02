"""Cursor overlay: injected on every document, follows the mouse, pulses on mousedown."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from demo_smoke import chrome, cursor

KIT = Path(__file__).resolve().parents[1]
APP_DIR = KIT / "tests" / "fixtures" / "app"
CHROME_DEFAULT = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"


def _env() -> None:
    if "DEMO_SMOKE_CHROME" not in os.environ and Path(CHROME_DEFAULT).exists():
        os.environ["DEMO_SMOKE_CHROME"] = CHROME_DEFAULT


def _serve_dir():
    try:
        from tests.fixtures.serve import serve_dir
    except ImportError:
        spec = importlib.util.spec_from_file_location("fixture_serve", KIT / "tests" / "fixtures" / "serve.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        serve_dir = mod.serve_dir
    return serve_dir


def _need_chrome() -> None:
    _env()
    if not chrome.find_chrome():
        pytest.skip("no Chrome binary available (set DEMO_SMOKE_CHROME)")


def test_cursor_js_is_self_contained():
    assert isinstance(cursor.CURSOR_JS, str)
    for needle in ("mousemove", "mousedown", "pointer-events:none", "z-index:2147483647", cursor.CURSOR_ID):
        assert needle in cursor.CURSOR_JS


def test_cursor_follows_mouse_and_survives_navigation(tmp_path):
    _need_chrome()
    with _serve_dir()(APP_DIR) as base, chrome.launch(tmp_path, {"width": 1024, "height": 640}, headless=True) as session:
        cursor.install(session.cdp)
        page = session.page
        page.goto(base + "/index.html", wait_until="load")
        el = page.locator("#" + cursor.CURSOR_ID)
        assert el.count() == 1
        assert page.evaluate("() => getComputedStyle(document.getElementById('demo-smoke-cursor')).pointerEvents") == "none"

        page.mouse.move(100, 200, steps=5)
        assert el.get_attribute("data-x") == "100"
        assert el.get_attribute("data-y") == "200"
        assert page.evaluate("() => document.getElementById('demo-smoke-cursor').style.opacity") == "1"

        page.mouse.down()
        page.mouse.up()
        assert el.get_attribute("data-downs") == "1"

        # Cursor is re-injected on the next document.
        page.goto(base + "/login.html", wait_until="load")
        assert page.locator("#" + cursor.CURSOR_ID).count() == 1
        # The overlay never becomes part of the visible page text.
        assert "demo-smoke" not in page.evaluate("() => document.body.innerText")


def test_chrome_session_bounds_and_clean_close(tmp_path):
    _need_chrome()
    session = chrome.launch(tmp_path, {"width": 800, "height": 600}, headless=True)
    try:
        assert set(session.window_bounds) == {"x", "y", "width", "height"}
        assert session.page.viewport_size == {"width": 800, "height": 600}
        assert session.version.get("Browser", "").startswith(("Chrome", "Chromium", "HeadlessChrome"))
        assert (tmp_path / "chrome-profile").is_dir()
        assert "--headless=new" in chrome.chrome_args(session.chrome_path, session.port, session.profile_dir,
                                                       session.viewport, True)
    finally:
        session.close()
    session.close()  # idempotent
    assert session._proc.poll() is not None, "Chrome process should be gone after close()"
    assert not (tmp_path / "chrome-profile").exists(), "close() removes the profile (it holds the app session)"
    assert (tmp_path / "logs" / "chrome.log").exists()


def test_launch_error_is_one_line(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_SMOKE_CHROME", str(tmp_path / "definitely-not-chrome"))
    with pytest.raises(chrome.ChromeError) as excinfo:
        chrome.launch(tmp_path, {"width": 800, "height": 600}, headless=True)
    assert "\n" not in str(excinfo.value)
    assert "definitely-not-chrome" in str(excinfo.value)
