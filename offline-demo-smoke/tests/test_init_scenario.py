"""``init-scenario`` writes a scaffold the kit's validator accepts (once the todos are gone)."""
from __future__ import annotations

import builtins
import json
from pathlib import Path

import pytest

from demo_smoke import onboard_scenario, scenario


def run(*argv):
    return onboard_scenario.main([str(a) for a in argv])


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_scaffold_from_flags(tmp_path: Path, capsys):
    out = tmp_path / "scenarios" / "chat.json"
    code = run("init-scenario", "--name", "Chat with Manuals", "--url", "http://localhost:3000/",
               "--out", out,
               "--step", "Open the app :: open the manuals page from the nav",
               "--step", "Upload a manual :: upload a PDF, a chip with its name appears",
               "--step", "Ask a question :: type a question, an answer with a [1] citation shows")
    assert code == 0
    data = _load(out)
    assert data["name"] == "Chat with Manuals"
    assert data["slug"] == "chat-with-manuals"
    assert data["app_url"] == "http://localhost:3000"
    assert data["login"] == {"type": "none"}
    assert data["intro"] and "Chat with Manuals" in data["intro"]
    assert data["outro"]
    assert [s["id"] for s in data["steps"]] == ["open-the-app", "upload-a-manual", "ask-a-question"]
    for s in data["steps"]:
        assert s["actions"] == [] and s["expect"] == []
        assert s["todo"] and "inspect" in s["todo"]
        assert s["narration"].endswith(".")
    assert "upload a PDF" in data["steps"][1]["todo"]
    # valid for scenario.validate once the todo markers are removed
    stripped = _load(out)
    onboard_scenario.strip_todos(stripped)
    assert scenario.validate(stripped) == []
    # and the kit's own validate command tolerates + reports them
    assert run("validate", out) == 0
    out_text = capsys.readouterr().out
    assert "3 steps" in out_text
    assert out_text.count("todo") >= 3 or out_text.count("warning") >= 3


def test_scaffold_default_step_and_default_path(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert run("init-scenario", "--name", "Legion Reports!", "--url", "http://127.0.0.1:8080") == 0
    out = tmp_path / "scenarios" / "legion-reports.json"
    assert out.is_file()
    data = _load(out)
    assert len(data["steps"]) == 1 and data["steps"][0]["todo"]
    onboard_scenario.strip_todos(data)
    assert scenario.validate(data) == []


def test_scaffold_form_login(tmp_path: Path, capsys):
    out = tmp_path / "s.json"
    assert run("init-scenario", "--name", "Secure App", "--url", "https://app.local", "--out", out,
               "--login", "form", "--username-env", "APP_USER", "--password-env", "APP_PASS",
               "--login-url", "/signin", "--success-selector", "nav .user",
               "--step", "Open") == 0
    login = _load(out)["login"]
    assert login["type"] == "form"
    assert login["username_env"] == "APP_USER" and login["password_env"] == "APP_PASS"
    assert login["url"] == "/signin" and login["success_selector"] == "nav .user"
    assert login["username_selector"] and login["password_selector"] and login["submit_selector"]
    data = _load(out)
    onboard_scenario.strip_todos(data)
    assert scenario.validate(data) == []
    text = capsys.readouterr().out
    assert "creds set APP_USER" in text and "creds set APP_PASS" in text


def test_scaffold_basic_login_defaults(tmp_path: Path):
    out = tmp_path / "b.json"
    assert run("init-scenario", "--name", "Basic", "--url", "http://h:1", "--out", out, "--login", "basic") == 0
    login = _load(out)["login"]
    assert login == {"type": "basic", "username_env": "DEMO_USER", "password_env": "DEMO_PASS"}


def test_duplicate_step_titles_get_unique_ids(tmp_path: Path):
    out = tmp_path / "d.json"
    assert run("init-scenario", "--name", "Dup", "--url", "http://h:1", "--out", out,
               "--step", "Click", "--step", "Click", "--step", "click!") == 0
    ids = [s["id"] for s in _load(out)["steps"]]
    assert ids == ["click", "click-2", "click-3"]
    data = _load(out)
    onboard_scenario.strip_todos(data)
    assert scenario.validate(data) == []


def test_refuses_to_overwrite_without_force(tmp_path: Path, capsys):
    out = tmp_path / "x.json"
    out.write_text("{}", encoding="utf-8")
    assert run("init-scenario", "--name", "X", "--url", "http://h:1", "--out", out) == 4
    assert "exists" in capsys.readouterr().err
    assert out.read_text(encoding="utf-8") == "{}"
    assert run("init-scenario", "--name", "X", "--url", "http://h:1", "--out", out, "--force") == 0
    assert _load(out)["name"] == "X"


@pytest.mark.parametrize("argv", [
    ["--url", "http://h:1"],                                   # no name
    ["--name", "X"],                                           # no url
    ["--name", "X", "--url", "localhost:3000"],                # not http(s)
    ["--name", "X", "--url", "http://h:1", "--login", "form", "--username-env", "bad-name"],
    ["--name", "  ", "--url", "http://h:1"],
])
def test_bad_input_exit_4(tmp_path: Path, argv, capsys):
    out = tmp_path / "bad.json"
    assert run("init-scenario", *argv, "--out", out) == 4
    assert not out.exists()
    assert "error:" in capsys.readouterr().err


def test_unknown_login_choice_is_usage_error(tmp_path: Path):
    assert run("init-scenario", "--name", "X", "--url", "http://h:1", "--login", "oauth",
               "--out", tmp_path / "x.json") == 4


def test_parse_step_arg():
    assert onboard_scenario.parse_step_arg("Open :: open it") == ("Open", "open it")
    assert onboard_scenario.parse_step_arg("Just a title") == ("Just a title", "Just a title")
    assert onboard_scenario.parse_step_arg(" :: only desc") == ("only desc", "only desc")


def test_interactive_prompts_via_input(tmp_path: Path, monkeypatch):
    answers = iter([
        "Chat with Manuals",         # name
        "",                          # url -> default http://localhost:3000
        "form",                      # login type
        "",                          # username env -> DEMO_USER
        "CWM_PASS",                  # password env
        "Open the app", "open it from the nav",
        "Ask", "",                   # description defaults to the title
        "",                          # blank title ends the step loop
    ])
    seen = []

    def fake_input(prompt=""):
        seen.append(prompt)
        return next(answers)

    monkeypatch.setattr(builtins, "input", fake_input)
    out = tmp_path / "i.json"
    assert run("init-scenario", "--interactive", "--out", out) == 0
    data = _load(out)
    assert data["name"] == "Chat with Manuals"
    assert data["app_url"] == "http://localhost:3000"
    assert data["login"]["type"] == "form"
    assert data["login"]["username_env"] == "DEMO_USER"
    assert data["login"]["password_env"] == "CWM_PASS"
    assert [s["id"] for s in data["steps"]] == ["open-the-app", "ask"]
    assert "open it from the nav" in data["steps"][0]["todo"]
    assert data["steps"][1]["todo"].startswith("Ask")
    assert any("Feature name" in p for p in seen) and any("Step 1" in p for p in seen)
    onboard_scenario.strip_todos(data)
    assert scenario.validate(data) == []


def test_interactive_keeps_flag_values_and_steps(tmp_path: Path, monkeypatch):
    def fake_input(prompt=""):
        raise AssertionError(f"unexpected prompt {prompt!r}")

    monkeypatch.setattr(builtins, "input", fake_input)
    out = tmp_path / "j.json"
    assert run("init-scenario", "--interactive", "--name", "N", "--url", "http://h:1", "--login", "none",
               "--step", "One", "--out", out) == 0
    assert [s["id"] for s in _load(out)["steps"]] == ["one"]


def test_interactive_eof_is_cancelled(tmp_path: Path, monkeypatch, capsys):
    def fake_input(prompt=""):
        raise EOFError

    monkeypatch.setattr(builtins, "input", fake_input)
    assert run("init-scenario", "--interactive", "--out", tmp_path / "k.json") == 130
    assert "cancelled" in capsys.readouterr().err
