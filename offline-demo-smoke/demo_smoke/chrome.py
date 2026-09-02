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
# Room for the tab strip + toolbar when the window is not headless (initial guess;
# ChromeSession measures the real insets after launch and resizes the window).
HEADED_CHROME_UI_HEIGHT = 88
# Blank page a fresh session is parked on (see ChromeSession._attach).
SETTLE_URL = "data:text/html,<title>demo-smoke</title>"


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
    try:
        # legacy + Chrome-for-Testing layouts
        from demo_smoke.env import playwright_chrome_patterns
    except ImportError:
        patterns = ["/opt/pw-browsers/chromium-*/chrome-linux*/chrome",
                    str(Path.home() / ".cache/ms-playwright/chromium-*/chrome-linux*/chrome")]
    else:
        patterns = playwright_chrome_patterns()
    for pat in patterns:
        candidates += sorted(glob.glob(pat), reverse=True)
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
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-sync",
    ]
    if headless:
        args.append("--headless=new")
    if sys.platform.startswith("linux") and hasattr(os, "geteuid") and os.geteuid() == 0:
        args.append("--no-sandbox")  # Chrome refuses to run as root otherwise
    args.append("about:blank")
    return args


def _wait_for_devtools(port: int, proc: subprocess.Popen, log_path: Path, timeout: float) -> dict:
    url = f"http://127.0.0.1:{port}/json/version"
    # Never route the loopback DevTools probe through HTTP_PROXY (urllib honours the
    # proxy env vars for every host that is not listed in no_proxy).
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    last_err = ""
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise ChromeError(f"Chrome exited with code {proc.returncode} during startup ({_log_tail(log_path)})")
        try:
            with opener.open(url, timeout=1.0) as resp:
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
    session), ``window_bounds`` ({x, y, width, height}, in DIPs), ``ui_insets``
    ({x, y}: browser UI between the window origin and the page area, DIPs),
    ``device_scale_factor`` (physical pixels per DIP, e.g. 2.0 on Retina / 125%
    Windows scaling = 1.25), ``port``, ``chrome_path``, ``profile_dir``,
    ``viewport``, ``headless``, ``version``.
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
        # Browser UI (tab strip/toolbar, or the area --headless=new reserves for it)
        # between the window size and the page area, measured after launch.
        self.ui_insets: dict = {"x": 0, "y": 0 if headless else HEADED_CHROME_UI_HEIGHT}
        self.device_scale_factor: float = 1.0
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
        self.cdp = self.context.new_cdp_session(self.page)
        try:
            # Headless Chrome only finishes laying out its (invisible) window UI on the
            # first navigation away from the initial about:blank; the page area moves
            # by ~50 px then.  Leave about:blank first so the fit below measures the
            # settled area instead of a value that changes under the first capture.
            self.page.goto(SETTLE_URL, wait_until="load", timeout=STARTUP_TIMEOUT_S * 1000)
        except Exception:
            log.debug("settle navigation failed", exc_info=True)
        self.device_scale_factor = self._read_scale_factor()
        self._fit_window_to_viewport()
        self.page.set_viewport_size({"width": int(self.viewport["width"]), "height": int(self.viewport["height"])})
        self.window_bounds = self._read_window_bounds()

    def _read_scale_factor(self) -> float:
        """``window.devicePixelRatio`` before any viewport emulation: the OS backing
        scale the screen grabbers see (Retina 2.0, Windows 125% = 1.25)."""
        try:
            dpr = float(self.page.evaluate("window.devicePixelRatio") or 1.0)
            return dpr if dpr > 0 else 1.0
        except Exception:
            log.debug("could not read devicePixelRatio", exc_info=True)
            return 1.0

    def _inner_size(self) -> tuple[int, int]:
        w, h = self.page.evaluate("[window.innerWidth, window.innerHeight]")
        return int(w), int(h)

    def _probe_surface_size(self, max_ms: int = 1500) -> tuple[int, int] | None:
        """The real page area as the screencast sees it (``deviceWidth``/``deviceHeight``
        of a throwaway screencast's frames).  Producing frames is also what makes
        headless Chrome finish laying out its invisible UI, so the value settles here
        rather than moving under the first real capture.  ``None`` if no frame came."""
        cdp = self.cdp
        sizes: list[tuple[int, int]] = []

        def on_frame(event: dict) -> None:
            meta = event.get("metadata") or {}
            if meta.get("deviceWidth") and meta.get("deviceHeight"):
                sizes.append((int(meta["deviceWidth"]), int(meta["deviceHeight"])))
            try:
                cdp.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]})
            except Exception:
                log.debug("ignored error", exc_info=True)

        cdp.on("Page.screencastFrame", on_frame)
        try:
            cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 30, "maxWidth": 8192, "maxHeight": 8192,
                                              "everyNthFrame": 1})
            waited = 0
            while waited < max_ms:
                # repaint so frames keep coming (about:blank is otherwise static)
                self.page.evaluate("on => { document.documentElement.style.backgroundColor = on ? '#fffffe' : ''; }",
                                   (waited // 100) % 2 == 0)
                self.page.wait_for_timeout(100)
                waited += 100
                if len(sizes) >= 3 and sizes[-1] == sizes[-2] == sizes[-3]:
                    break
        finally:
            try:
                cdp.send("Page.stopScreencast")
            except Exception:
                log.debug("ignored error", exc_info=True)
            try:
                self.page.wait_for_timeout(50)
                cdp.remove_listener("Page.screencastFrame", on_frame)
                self.page.evaluate("document.documentElement.style.backgroundColor = ''")
            except Exception:
                log.debug("ignored error", exc_info=True)
        return sizes[-1] if sizes else None

    def _fit_window_to_viewport(self, rounds: int = 4) -> None:
        """Resize the window until the real page area equals the viewport.

        ``--window-size`` is the outer window: headed Chrome spends part of it on
        the tab strip and toolbar, and ``--headless=new`` reserves ~140 px for the
        same UI even though nothing is drawn.  Playwright's ``set_viewport_size``
        only *emulates* the viewport for layout; the CDP screencast (and an OS
        screen grab) capture the real page area, so a 1280x720 headless window
        yielded 1280x580 frames.  Measure the area as the screencast reports it,
        fix the window bounds, and repeat until they agree (the insets settle
        over the first frames).  Never raises; a failure leaves the window as is.
        """
        want_w, want_h = int(self.viewport["width"]), int(self.viewport["height"])
        bcdp = None
        try:
            for _ in range(rounds):
                real = self._probe_surface_size() or self._inner_size()
                dx, dy = want_w - real[0], want_h - real[1]
                if not dx and not dy:
                    break
                if bcdp is None:
                    target_id = self.cdp.send("Target.getTargetInfo")["targetInfo"]["targetId"]
                    bcdp = self.browser.new_browser_cdp_session()
                win = bcdp.send("Browser.getWindowForTarget", {"targetId": target_id})
                bounds = win["bounds"]
                bcdp.send("Browser.setWindowBounds", {
                    "windowId": win["windowId"],
                    "bounds": {"width": int(bounds["width"]) + dx, "height": int(bounds["height"]) + dy},
                })
                # Chrome has no UI left of the page: a width change moves nothing horizontally,
                # only the height delta is browser UI above the page area.
                self.ui_insets = {"x": self.ui_insets["x"], "y": self.ui_insets["y"] + dy}
                self.page.wait_for_timeout(200)
            else:
                log.debug("window did not settle on the %sx%s viewport after %s rounds", want_w, want_h, rounds)
        except Exception:
            log.debug("could not fit the window to the viewport", exc_info=True)
        finally:
            if bcdp is not None:
                try:
                    bcdp.detach()
                except Exception:
                    log.debug("ignored error", exc_info=True)

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


def _fresh_profile_dir(out: Path, port: int) -> Path:
    """``<out>/chrome-profile``, guaranteed empty.  A leftover that cannot be removed
    (a Chrome from an earlier attempt still holding files) must not be reused silently:
    its cookies/localStorage would change what the next attempt tests, so fall back to
    a unique ``chrome-profile-<port>`` and say so in the log."""
    profile_dir = out / "chrome-profile"
    if profile_dir.exists():
        shutil.rmtree(profile_dir, ignore_errors=True)
    if profile_dir.exists():
        fallback = out / f"chrome-profile-{port}"
        log.warning("could not remove the previous Chrome profile %s; using a fresh %s instead",
                    profile_dir, fallback)
        profile_dir = fallback
        if profile_dir.exists():
            shutil.rmtree(profile_dir, ignore_errors=True)
    profile_dir.mkdir(parents=True, exist_ok=True)
    return profile_dir


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

    port = _free_port()
    profile_dir = _fresh_profile_dir(out, port)
    logs = out / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / "chrome.log"
    log_file = open(log_path, "ab")  # noqa: SIM115 - handed to Popen, closed in close()

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
