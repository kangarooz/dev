"""Browser tests for demo_smoke.drive against the static fixture app."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

from demo_smoke import chrome, drive

KIT = Path(__file__).resolve().parents[1]
APP_DIR = KIT / "tests" / "fixtures" / "app"
SCEN_DIR = KIT / "tests" / "fixtures" / "scenarios"
CHROME_DEFAULT = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"


def _env() -> None:
    if "DEMO_SMOKE_CHROME" not in os.environ and Path(CHROME_DEFAULT).exists():
        os.environ["DEMO_SMOKE_CHROME"] = CHROME_DEFAULT
    if "DEMO_SMOKE_FFMPEG" not in os.environ:
        import imageio_ffmpeg

        os.environ["DEMO_SMOKE_FFMPEG"] = imageio_ffmpeg.get_ffmpeg_exe()


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


def load_scenario(name: str, base_url: str) -> dict:
    """Load a fixture scenario without depending on demo_smoke.scenario (relative files resolve via _dir)."""
    path = SCEN_DIR / name
    data = json.loads(path.read_text(encoding="utf-8"))
    data["_dir"] = path.parent
    data["app_url"] = base_url
    return data


# --------------------------------------------------------------------------- pure helpers
def test_resolve_url():
    assert drive._resolve_url("http://h:1", "/") == "http://h:1/"
    assert drive._resolve_url("http://h:1/", "/?auth=1") == "http://h:1/?auth=1"
    assert drive._resolve_url("http://h:1", "login.html") == "http://h:1/login.html"
    assert drive._resolve_url("http://h:1", "https://other/x") == "https://other/x"


def test_expect_summary():
    assert drive.expect_summary({"selector": ".a", "count_min": 2, "contains": "x"}) == 'selector .a count>=2 contains "x"'
    assert drive.expect_summary({"text": "hi"}) == 'text contains "hi"'
    assert drive.expect_summary({"not_text": "hi"}) == 'text does not contain "hi"'
    assert drive.expect_summary({"url_contains": "auth"}) == 'url contains "auth"'


def test_login_reports_missing_env(monkeypatch):
    monkeypatch.delenv("DEMO_SMOKE_MISSING_USER", raising=False)
    scenario = {"app_url": "http://x", "login": {"type": "form", "username_env": "DEMO_SMOKE_MISSING_USER",
                                                  "password_env": "DEMO_SMOKE_MISSING_PASS"}}
    err = drive.login(None, scenario)
    assert err == "login: environment variable DEMO_SMOKE_MISSING_USER is not set"
    assert drive.login(None, {"login": {"type": "none"}}) is None
    assert "unsupported" in drive.login(None, {"login": {"type": "oauth"}})


# --------------------------------------------------------------------------- browser
def test_dryrun_pass(tmp_path):
    _need_chrome()
    with _serve_dir()(APP_DIR) as base:
        scenario = load_scenario("fixture-pass.json", base)
        result = drive.dryrun(scenario, tmp_path, headless=True)

    assert result["verdict"] == "PASS"
    assert result["exit_code"] == 0
    assert result["attempts"] == 1
    assert [s["id"] for s in result["steps"]] == ["open", "upload", "ask"]
    assert all(s["status"] == "PASS" for s in result["steps"])
    for key in ("id", "title", "status", "expected", "observed", "screenshot", "seconds", "error"):
        assert key in result["steps"][0]
    assert result["console_errors"] == []
    assert result["failed_requests"] == []

    logs = tmp_path / "logs"
    for name in ("step-01-open.png", "step-02-upload.png", "step-03-ask.png", "after-ask.png", "dryrun.json"):
        assert (logs / name).exists(), name
    assert not list(logs.glob("failure-*.html"))
    md = (logs / "smoke-results.md").read_text(encoding="utf-8")
    assert "**PASS**" in md
    assert "| ask |" in md
    saved = json.loads((logs / "dryrun.json").read_text(encoding="utf-8"))
    assert saved["verdict"] == "PASS"
    # the answer step waits ~1.2 s for the delayed answer, which is below the wait-window threshold
    ask = result["steps"][2]
    assert "inspect" in ask["observed"]
    assert ask["wait_windows"] == []


def test_dryrun_fail_captures_failure_context(tmp_path):
    _need_chrome()
    with _serve_dir()(APP_DIR) as base:
        scenario = load_scenario("fixture-fail.json", base)
        result = drive.dryrun(scenario, tmp_path, headless=True)

    assert result["verdict"] == "FAIL"
    assert result["exit_code"] == 2
    assert result["attempts"] == 2, "a FAIL must be retried once"
    statuses = {s["id"]: s["status"] for s in result["steps"]}
    assert statuses == {"open": "PASS", "ask": "FAIL", "never": "SKIPPED"}
    ask = next(s for s in result["steps"] if s["id"] == "ask")
    assert ask["error"] and "\n" not in ask["error"]
    assert "not found" in ask["error"]
    assert ask["wait_windows"] and ask["wait_windows"][0][1] - ask["wait_windows"][0][0] >= 1.5
    assert any("No manuals uploaded" in e for e in result["console_errors"])
    assert any(r["status"] == 404 and "/api/answer" in r["url"] for r in result["failed_requests"])
    never = next(s for s in result["steps"] if s["id"] == "never")
    assert never["screenshot"] is None and "skipped" in never["error"]

    logs = tmp_path / "logs"
    html = (logs / "failure-ask.html").read_text(encoding="utf-8")
    assert "Chat with Manuals" in html
    md = (logs / "smoke-results.md").read_text(encoding="utf-8")
    assert "**FAIL**" in md and "No manuals uploaded" in md and "/api/answer" in md
    assert "attempt 1 failed" in md and "attempt 2 failed" in md


def test_dryrun_login_form(tmp_path, monkeypatch):
    _need_chrome()
    monkeypatch.setenv("DEMO_SMOKE_USER", "demo")
    monkeypatch.setenv("DEMO_SMOKE_PASS", "secret")
    with _serve_dir()(APP_DIR) as base:
        scenario = load_scenario("fixture-login.json", base)
        result = drive.dryrun(scenario, tmp_path, headless=True)
    assert result["verdict"] == "PASS", [s["error"] for s in result["steps"]]
    assert result["attempts"] == 1
    open_step = result["steps"][0]
    assert "auth=1" in open_step["observed"]


def test_dryrun_basic_auth(tmp_path, monkeypatch):
    _need_chrome()
    monkeypatch.setenv("DEMO_SMOKE_USER", "demo")
    monkeypatch.setenv("DEMO_SMOKE_PASS", "secret")
    with _serve_dir()(APP_DIR, basic_auth=("demo", "secret")) as base:
        scenario = load_scenario("fixture-pass.json", base)
        scenario["login"] = {"type": "basic", "username_env": "DEMO_SMOKE_USER", "password_env": "DEMO_SMOKE_PASS"}
        result = drive.dryrun(scenario, tmp_path, headless=True)
    assert result["verdict"] == "PASS", [s["error"] for s in result["steps"]]


def test_selector_expectation_counts_visible_elements_only(tmp_path):
    """schema.json: 'At least one visible element matches' - a hidden placeholder must not pass."""
    _need_chrome()
    with chrome.launch(tmp_path, {"width": 640, "height": 480}, headless=True) as session:
        page = session.page
        page.set_content('<div class="answer" style="display:none">inspect hidden</div>'
                         '<div class="chip">one</div><div class="chip" hidden>two</div>')
        ok, obs = drive.check_expectation(page, {"selector": ".answer"})
        assert ok is False and "0 visible" in obs
        ok, _ = drive.check_expectation(page, {"selector": ".answer", "contains": "inspect"})
        assert ok is False
        ok, obs = drive.check_expectation(page, {"selector": ".chip", "count_min": 1})
        assert ok is True and "1 visible" in obs
        ok, _ = drive.check_expectation(page, {"selector": ".chip", "count_min": 2})
        assert ok is False
        page.set_content('<div class="answer">Ladders must be inspected</div>')
        ok, _ = drive.check_expectation(page, {"selector": ".answer", "contains": "inspect"})
        assert ok is True


def test_dryrun_launch_failure_is_drive_error(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_SMOKE_CHROME", str(tmp_path / "no-such-chrome"))
    with pytest.raises(drive.DriveError) as excinfo:
        drive.dryrun({"app_url": "http://127.0.0.1:1", "steps": []}, tmp_path, headless=True)
    assert "\n" not in str(excinfo.value)
