"""Launch a real Chrome/Chromium with remote debugging and attach Playwright over CDP.

We start the browser ourselves (instead of ``playwright.chromium.launch``) so
the very same process can be captured by the OS screen grabber, with a known
window position and size, and so that a system Chrome can be used offline
without any Playwright browser download.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_VIEWPORT = {"width": 1920, "height": 1080}
STARTUP_TIMEOUT_S = 20.0
TERMINATE_GRACE_S = 5.0
# Room for the tab strip + toolbar when the window is not headless.
HEADED_CHROME_UI_HEIGHT = 88


class ChromeError(RuntimeError):
    """Chrome could not be found, started, or attached to (one-line message)."""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _candidate_paths() -> list[str]:
    system = platform.system()
    candidates: list[str] = []
    if system == "Windows":
        for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(env_name)
            if base:
                candidates.append(str(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"))
                candidates.append(str(Path(base) / "Chromium" / "Application" / "chrome.exe"))
                candidates.append(str(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"))
    elif system == "Darwin":
        candidates += [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            str(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ]
    else:
        candidates += [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/snap/bin/chromium",
        ]
    for name in ("google-chrome", "chrome", "chromium", "chromium-browser", "msedge"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    candidates += sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"), reverse=True)
    candidates += sorted(glob.glob(str(Path.home() / ".cache/ms-playwright/chromium-*/chrome-linux/chrome")), reverse=True)
    return candidates


def find_chrome() -> str | None:
    """``DEMO_SMOKE_CHROME`` -> ``demo_smoke.env.find_chrome`` -> common paths."""
    env_path = os.environ.get("DEMO_SMOKE_CHROME")
    if env_path:
        return env_path
    try:
        # lazy: env.py is another builder's module
        from demo_smoke.env import find_chrome as env_find
    except ImportError:
        log.debug("ignored error", exc_info=True)
        env_find = None
    if env_find is not None:
        try:
            found = env_find()
            if found:
                return str(found)
        except Exception:
            log.debug("ignored error", exc_info=True)
    for cand in _candidate_paths():
        if cand and Path(cand).is_file():
            return cand
    return None


def chrome_args(chrome: str, port: int, profile_dir: Path, viewport: dict, headless: bool) -> list[str]:
    """Build the Chrome command line (pure, so it can be unit-tested)."""
    width = int(viewport.get("width", DEFAULT_VIEWPORT["width"]))
    height = int(viewport.get("height", DEFAULT_VIEWPORT["height"]))
    window_h = height if headless else height + HEADED_CHROME_UI_HEIGHT
    args = [
        chrome,
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        f"--user-data-dir={profile_dir}",
        f"--window-size={width},{window_h}",
        "--window-position=0,0",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate,InfiniteSessionRestore",
        "--disable-session-crashed-bubble",
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        "--disable-backgrounding-occluded-windows",
        "--hide-crash-restore-bubble",
    ]
    if headless:
        args.append("--headless=new")
    if sys.platform.startswith("linux") and hasattr(os, "geteuid") and os.geteuid() == 0:
        args.append("--no-sandbox")  # Chrome refuses to run as root otherwise
    args.append("about:blank")
    return args


def _wait_for_devtools(port: int, proc: subprocess.Popen, log_path: Path, timeout: float) -> dict:
    url = f"http://127.0.0.1:{port}/json/version"
    deadline = time.monotonic() + timeout
    last_err = ""
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise ChromeError(f"Chrome exited with code {proc.returncode} during startup ({_log_tail(log_path)})")
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last_err = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
            time.sleep(0.1)
    raise ChromeError(f"Chrome DevTools endpoint on port {port} did not answer within {timeout:.0f} s ({last_err})")


def _log_tail(log_path: Path, limit: int = 300) -> str:
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return "no log"
    if not text:
        return "empty log"
    return " | ".join(text.splitlines()[-3:])[-limit:]


class ChromeSession:
    """A running Chrome process plus the Playwright objects attached to it.

    Attributes: ``page``, ``context``, ``browser``, ``cdp`` (page-level CDP
    session), ``window_bounds`` ({x, y, width, height}), ``port``,
    ``chrome_path``, ``profile_dir``, ``viewport``, ``headless``, ``version``.
    Use as a context manager or call ``close()``.
    """

    def __init__(self, proc: subprocess.Popen, chrome_path: str, port: int, profile_dir: Path,
                 viewport: dict, headless: bool, log_file, log_path: Path):
        self._proc = proc
        self._log_file = log_file
        self.log_path = log_path
        self.chrome_path = chrome_path
        self.port = port
        self.profile_dir = profile_dir
        self.viewport = dict(viewport)
        self.headless = headless
        self.version: dict = {}
        self._pw = None
        self.browser = None
        self.context = None
        self.page = None
        self.cdp = None
        self.window_bounds: dict = {"x": 0, "y": 0, "width": int(viewport["width"]), "height": int(viewport["height"])}
        self._closed = False

    @property
    def pid(self) -> int:
        return self._proc.pid

    def _attach(self) -> None:
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{self.port}", timeout=STARTUP_TIMEOUT_S * 1000)
        contexts = self.browser.contexts
        self.context = contexts[0] if contexts else self.browser.new_context()
        pages = self.context.pages
        self.page = pages[0] if pages else self.context.new_page()
        self.page.set_viewport_size({"width": int(self.viewport["width"]), "height": int(self.viewport["height"])})
        self.cdp = self.context.new_cdp_session(self.page)
        self.window_bounds = self._read_window_bounds()

    def _read_window_bounds(self) -> dict:
        fallback = dict(self.window_bounds)
        try:
            target_id = None
            try:
                target_id = self.cdp.send("Target.getTargetInfo")["targetInfo"]["targetId"]
            except Exception:
                log.debug("ignored error", exc_info=True)
            bcdp = self.browser.new_browser_cdp_session()
            try:
                if not target_id:
                    infos = bcdp.send("Target.getTargets")["targetInfos"]
                    pages = [t for t in infos if t.get("type") == "page"]
                    if not pages:
                        return fallback
                    target_id = pages[0]["targetId"]
                bounds = bcdp.send("Browser.getWindowForTarget", {"targetId": target_id})["bounds"]
            finally:
                try:
                    bcdp.detach()
                except Exception:
                    log.debug("ignored error", exc_info=True)
            return {
                "x": int(bounds.get("left", 0)),
                "y": int(bounds.get("top", 0)),
                "width": int(bounds.get("width", fallback["width"])),
                "height": int(bounds.get("height", fallback["height"])),
            }
        except Exception:
            log.debug("ignored error", exc_info=True)
            return fallback

    def close(self) -> None:
        """Disconnect Playwright and stop the Chrome process. Idempotent; never raises."""
        if self._closed:
            return
        self._closed = True
        for step in (self._detach_cdp, self._close_browser, self._stop_playwright):
            try:
                step()
            except Exception:
                log.debug("ignored error", exc_info=True)
        self._terminate_process()
        try:
            if self._log_file is not None:
                self._log_file.close()
        except Exception:
            log.debug("ignored error", exc_info=True)

    def _detach_cdp(self) -> None:
        if self.cdp is not None:
            self.cdp.detach()

    def _close_browser(self) -> None:
        if self.browser is not None:
            self.browser.close()

    def _stop_playwright(self) -> None:
        if self._pw is not None:
            self._pw.stop()

    def _terminate_process(self) -> None:
        proc = self._proc
        if proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=TERMINATE_GRACE_S)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=TERMINATE_GRACE_S)
            except Exception:
                log.debug("ignored error", exc_info=True)
        except Exception:
            log.debug("ignored error", exc_info=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def launch(out: Path, viewport: dict, headless: bool = False) -> ChromeSession:
    """Start Chrome with a fresh profile under ``<out>/chrome-profile`` and attach Playwright.

    Raises ``ChromeError`` with a one-line message when Chrome is missing or
    does not come up.
    """
    out = Path(out)
    viewport = {"width": int((viewport or {}).get("width", DEFAULT_VIEWPORT["width"])),
                "height": int((viewport or {}).get("height", DEFAULT_VIEWPORT["height"]))}
    chrome = find_chrome()
    if not chrome:
        raise ChromeError("Chrome not found: install Google Chrome/Chromium or set DEMO_SMOKE_CHROME to the binary")
    if not Path(chrome).is_file():
        raise ChromeError(f"Chrome binary does not exist: {chrome}")

    profile_dir = out / "chrome-profile"
    if profile_dir.exists():
        shutil.rmtree(profile_dir, ignore_errors=True)
    profile_dir.mkdir(parents=True, exist_ok=True)
    logs = out / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / "chrome.log"
    log_file = open(log_path, "ab")  # noqa: SIM115 - handed to Popen, closed in close()

    port = _free_port()
    args = chrome_args(chrome, port, profile_dir, viewport, headless)
    env = dict(os.environ)
    env.setdefault("LANG", "C.UTF-8")
    try:
        proc = subprocess.Popen(args, stdout=log_file, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env)
    except OSError as exc:
        log_file.close()
        raise ChromeError(f"could not start Chrome ({chrome}): {exc}") from None

    session = ChromeSession(proc, chrome, port, profile_dir, viewport, headless, log_file, log_path)
    try:
        session.version = _wait_for_devtools(port, proc, log_path, STARTUP_TIMEOUT_S)
        session._attach()
    except ChromeError:
        session.close()
        raise
    except Exception as exc:
        log.debug("handled error", exc_info=True)
        session.close()
        msg = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
        raise ChromeError(f"could not attach Playwright to Chrome on port {port}: {msg}") from exc
    return session
