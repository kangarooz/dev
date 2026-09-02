"""``inspect URL``: selector picking (pure) and a headless run against tests/fixtures/app."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

from demo_smoke import onboard_scenario

KIT = Path(__file__).resolve().parents[1]
APP_DIR = KIT / "tests" / "fixtures" / "app"
LOGIN_SCENARIO = KIT / "tests" / "fixtures" / "scenarios" / "fixture-login.json"
CHROME_DEFAULT = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"


def run(*argv):
    return onboard_scenario.main([str(a) for a in argv])


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
    if "DEMO_SMOKE_CHROME" not in os.environ and Path(CHROME_DEFAULT).exists():
        os.environ["DEMO_SMOKE_CHROME"] = CHROME_DEFAULT
    from demo_smoke import chrome

    if not chrome.find_chrome():
        pytest.skip("no Chrome binary available (set DEMO_SMOKE_CHROME)")


# --------------------------------------------------------------------------- pure helpers


def _el(**kw) -> dict:
    base = {"index": 0, "tag": "input", "type": "", "id": "", "name": "", "placeholder": "", "text": "",
            "aria_label": "", "label": "", "role": "", "href": "", "visible": True, "disabled": False,
            "tag_index": 0}
    base.update(kw)
    return base


def test_classify():
    c = onboard_scenario.classify
    assert c(_el(tag="input", type="file")) == "file"
    assert c(_el(tag="button")) == "button"
    assert c(_el(tag="input", type="submit")) == "button"
    assert c(_el(tag="div", role="button")) == "button"
    assert c(_el(tag="a", href="/x")) == "link"
    assert c(_el(tag="select")) == "select"
    assert c(_el(tag="textarea")) == "textarea"
    assert c(_el(tag="input", type="")) == "input:text"
    assert c(_el(tag="input", type="password")) == "input:password"


def test_selector_candidates_prefer_id_then_name_then_placeholder():
    cands = onboard_scenario.selector_candidates(
        _el(tag="textarea", id="question", name="question", placeholder="Ask a question"))
    assert cands[:3] == ["#question", "textarea[name=question]", 'textarea[placeholder="Ask a question"]']
    assert cands[-1] == "textarea >> nth=0"
    btn = onboard_scenario.selector_candidates(_el(tag="button", type="submit", text="Ask", tag_index=2))
    assert btn == ['button:has-text("Ask")', "button >> nth=2"]
    file_in = onboard_scenario.selector_candidates(_el(tag="input", type="file", tag_index=1))
    assert file_in == ["input[type=file]", "input >> nth=1"]
    link = onboard_scenario.selector_candidates(_el(tag="a", text="Manuals", href="/manuals"))
    assert link[:2] == ['a:has-text("Manuals")', "a[href=/manuals]"]
    weird = onboard_scenario.selector_candidates(_el(tag="input", id="1st", name="a b", placeholder='say "hi"'))
    assert weird[:3] == ['[id="1st"]', 'input[name="a b"]', 'input[placeholder="say \\"hi\\""]']
    role = onboard_scenario.selector_candidates(_el(tag="div", role="button", text="Go"))
    assert role[0] == '[role=button]:has-text("Go")'


def test_choose_selector_picks_first_unique_and_falls_back():
    counts = {"#dup": 2, "input[name=q]": 1}
    sel, n = onboard_scenario.choose_selector(_el(id="dup", name="q"), lambda s: counts.get(s, 0))
    assert (sel, n) == ("input[name=q]", 1)
    sel, n = onboard_scenario.choose_selector(_el(id="dup"), lambda s: counts.get(s, 0))
    assert (sel, n) == ("#dup", 2)

    def boom(s):
        raise ValueError("unsupported selector")

    sel, n = onboard_scenario.choose_selector(_el(tag="button", text="Go", tag_index=3), boom)
    assert sel == "button >> nth=3" and n == 0


class _FakeLocator:
    def __init__(self, n):
        self._n = n

    def count(self):
        return self._n


class _FakePage:
    def __init__(self, elements, counts=None):
        self._elements = elements
        self._counts = counts or {}
        self.url = "http://fake/"

    def evaluate(self, js):
        return self._elements

    def locator(self, sel):
        return _FakeLocator(self._counts.get(sel, 1))


def test_collect_elements_caps_rows_dropping_links_first():
    els = [_el(index=i, tag="a", text=f"link {i}", href=f"/{i}") for i in range(70)]
    els.append(_el(index=70, tag="button", text="Go"))
    els.append(_el(index=71, tag="input", type="file", visible=False))
    els.append(_el(index=72, tag="button", text="Hidden", visible=False))
    rows, total = onboard_scenario.collect_elements(_FakePage(els), max_rows=60)
    assert total == 72                       # hidden button dropped, hidden file input kept
    assert len(rows) == 60
    kinds = [r["kind"] for r in rows]
    assert "button" in kinds and "file" in kinds
    assert rows[-2]["kind"] == "button" and rows[-1]["kind"] == "file"   # DOM order kept
    assert kinds.count("link") == 58
    rows_all, total_all = onboard_scenario.collect_elements(_FakePage(els), max_rows=100, include_hidden=True)
    assert total_all == 73 and len(rows_all) == 73


def test_format_table_marks_non_unique_and_hidden():
    rows = [{"kind": "button", "selector": "button", "unique": False, "matches": 3, "hint": "Go", "href": "",
             "disabled": False, "visible": True},
            {"kind": "link", "selector": "a:has-text(\"Docs\")", "unique": True, "matches": 1, "hint": "Docs",
             "href": "/docs", "disabled": True, "visible": False}]
    lines = onboard_scenario.format_table(rows)
    assert "selector" in lines[0]
    assert "(x3)" in lines[1]
    assert "-> /docs" in lines[2] and "[disabled]" in lines[2] and "[hidden]" in lines[2]


def test_inspect_rejects_non_http_url(tmp_path: Path, capsys):
    assert run("inspect", "ftp://x", "--headless", "--out", tmp_path / "o") == 4
    assert "http(s)" in capsys.readouterr().err
    log = json.loads((tmp_path / "o" / "logs" / "inspect.json").read_text(encoding="utf-8"))
    assert log["exit_code"] == 4


def test_inspect_bad_login_scenario_is_bad_input(tmp_path: Path, capsys):
    assert run("inspect", "http://127.0.0.1:1/", "--headless", "--login-from", tmp_path / "missing.json",
               "--out", tmp_path / "o") == 4
    assert "not found" in capsys.readouterr().err


# --------------------------------------------------------------------------- browser


@pytest.fixture
def app_url():
    _need_chrome()
    with _serve_dir()(APP_DIR) as base:
        yield base


def test_inspect_fixture_app_json(app_url, tmp_path: Path, capsys):
    out = tmp_path / "out"
    assert run("inspect", f"{app_url}/index.html", "--headless", "--json", "--out", out) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["title"] == "Chat with Manuals"
    by_kind = {}
    for e in res["elements"]:
        by_kind.setdefault(e["kind"], []).append(e)
    textarea = by_kind["textarea"][0]
    assert textarea["selector"] == "#question" and textarea["unique"]
    assert "Ask a question" in textarea["placeholder"]
    button = next(b for b in by_kind["button"] if b["text"] == "Ask")
    assert button["selector"] == "#ask-btn" and button["unique"] and button["type"] == "submit"
    file_input = by_kind["file"][0]
    assert file_input["selector"] == "#file-input" and file_input["unique"]
    assert file_input["label"] == "Upload manuals"
    assert res["shown"] == len(res["elements"]) <= 60
    log = json.loads((out / "logs" / "inspect.json").read_text(encoding="utf-8"))
    assert log["elements"] == res["elements"]
    # the chosen selectors really address the elements (one match each, as promised)
    assert all(e["matches"] == 1 for e in res["elements"])


def test_inspect_fixture_app_table(app_url, tmp_path: Path, capsys):
    assert run("inspect", f"{app_url}/login.html", "--headless", "--out", tmp_path / "out") == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0].startswith("inspect: ok") and "interactive elements" in lines[0]
    body = "\n".join(lines[1:])
    assert "#username" in body and "#password" in body and "#login-btn" in body
    assert "input:password" in body and "Sign in" in body


def test_inspect_login_from_scenario(app_url, tmp_path: Path, monkeypatch, capsys):
    data = json.loads(LOGIN_SCENARIO.read_text(encoding="utf-8"))
    data["app_url"] = app_url
    scen = tmp_path / "login.json"
    scen.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("DEMO_SMOKE_USER", "demo")
    monkeypatch.setenv("DEMO_SMOKE_PASS", "secret")
    # relative URL resolves against the scenario's app_url; the auth gate would bounce without login
    assert run("inspect", "/?auth=1", "--headless", "--json", "--login-from", scen, "--out", tmp_path / "o") == 0
    res = json.loads(capsys.readouterr().out)
    assert "auth=1" in res["final_url"] and "login.html" not in res["final_url"]
    assert any(e["selector"] == "#question" for e in res["elements"])


def test_inspect_login_failure_exit_2(app_url, tmp_path: Path, monkeypatch, capsys):
    data = json.loads(LOGIN_SCENARIO.read_text(encoding="utf-8"))
    data["app_url"] = app_url
    data["login"]["success_selector"] = "#never-appears"
    scen = tmp_path / "login.json"
    scen.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("DEMO_SMOKE_USER", "demo")
    monkeypatch.setenv("DEMO_SMOKE_PASS", "wrong")
    monkeypatch.setattr("demo_smoke.drive.LOGIN_TIMEOUT_MS", 2_000)
    assert run("inspect", f"{app_url}/?auth=1", "--headless", "--login-from", scen, "--out", tmp_path / "o") == 2
    assert "inspect: FAIL" in capsys.readouterr().out
