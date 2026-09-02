"""``validate SCENARIO``: step list, warnings (todo, empty actions, missing creds), exit 4 on errors."""
from __future__ import annotations

import json
from pathlib import Path

from demo_smoke import onboard_scenario

KIT = Path(__file__).resolve().parents[1]
EXAMPLE = KIT / "scenarios" / "example-chat-with-manuals.json"


def run(*argv):
    return onboard_scenario.main([str(a) for a in argv])


def _write(tmp_path: Path, data, name="s.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")
    return p


def _base() -> dict:
    return {
        "name": "Tiny", "app_url": "http://localhost:3000",
        "steps": [
            {"id": "open", "title": "Open", "actions": [{"goto": "/"}], "expect": [{"text": "Tiny"}]},
            {"id": "ask", "title": "Ask", "actions": [{"fill": {"selector": "textarea", "text": "hi"}},
                                                      {"click": "button"}],
             "expect": [{"selector": ".answer"}]},
        ],
    }


def test_example_scenario_is_valid_and_lists_steps(monkeypatch, capsys):
    monkeypatch.setenv("DEMO_USER", "u")
    monkeypatch.setenv("DEMO_PASS", "p")
    assert run("validate", EXAMPLE) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0].startswith("validate: ok") and "4 steps" in lines[0] and "login=form" in lines[0]
    for sid in ("open", "upload", "ask", "citation"):
        assert any(sid in ln and "actions=" in ln for ln in lines[1:])
    assert "goto,click,wait_for" in out
    assert "0 warnings" in lines[0] and "warning:" not in out


def test_warns_on_todo_and_empty_actions(tmp_path: Path, capsys):
    data = _base()
    data["steps"][0]["todo"] = "open the app from the nav"
    data["steps"][1]["actions"] = []
    data["steps"][1]["expect"] = []
    p = _write(tmp_path, data)
    assert run("validate", p) == 0
    out = capsys.readouterr().out
    assert "2 warnings" in out or "3 warnings" in out
    assert "warning: step open: open the app from the nav" in out
    assert "warning: step ask: no actions" in out
    assert "no expectations" in out


def test_missing_login_env_is_a_warning_not_an_error(tmp_path: Path, monkeypatch, capsys):
    data = _base()
    data["login"] = {"type": "basic", "username_env": "NO_SUCH_USER_X", "password_env": "NO_SUCH_PASS_X"}
    monkeypatch.delenv("NO_SUCH_USER_X", raising=False)
    monkeypatch.delenv("NO_SUCH_PASS_X", raising=False)
    p = _write(tmp_path, data)
    assert run("validate", p, "--env-file", tmp_path / "none.env") == 0
    out = capsys.readouterr().out
    assert "NO_SUCH_USER_X is not set" in out and "creds set NO_SUCH_PASS_X" in out


def test_login_env_from_env_file_silences_warning(tmp_path: Path, monkeypatch, capsys):
    data = _base()
    data["login"] = {"type": "basic", "username_env": "NO_SUCH_USER_Y", "password_env": "NO_SUCH_PASS_Y"}
    monkeypatch.delenv("NO_SUCH_USER_Y", raising=False)
    monkeypatch.delenv("NO_SUCH_PASS_Y", raising=False)
    env = tmp_path / ".env"
    env.write_text("NO_SUCH_USER_Y=a\nNO_SUCH_PASS_Y=b\n", encoding="utf-8")
    p = _write(tmp_path, data)
    assert run("validate", p, "--env-file", env) == 0
    out = capsys.readouterr().out
    assert "0 warnings" in out and "warning:" not in out


def test_missing_upload_file_is_a_warning(tmp_path: Path, capsys):
    data = _base()
    data["steps"][0]["actions"] = [{"upload": {"selector": "input[type=file]", "files": ["nope.pdf"]}}]
    p = _write(tmp_path, data)
    assert run("validate", p) == 0
    assert "upload file not found" in capsys.readouterr().out


def test_invalid_scenario_exit_4_with_messages(tmp_path: Path, capsys):
    data = _base()
    data["steps"][0]["actions"] = [{"clickk": "x"}]
    data["bogus"] = 1
    del data["name"]
    p = _write(tmp_path, data)
    assert run("validate", p) == 4
    cap = capsys.readouterr()
    assert cap.out.startswith("validate: INVALID") and "3 errors" in cap.out
    assert "unknown action 'clickk'" in cap.err
    assert "unknown top-level key 'bogus'" in cap.err
    assert "name must be" in cap.err


def test_invalid_json_exit_4(tmp_path: Path, capsys):
    p = _write(tmp_path, '{"name": "x",,}')
    assert run("validate", p) == 4
    assert "invalid JSON" in capsys.readouterr().err


def test_missing_file_exit_4(tmp_path: Path, capsys):
    assert run("validate", tmp_path / "absent.json") == 4
    assert "not found" in capsys.readouterr().err


def test_todo_alone_does_not_fail_validation(tmp_path: Path):
    """A scaffold from init-scenario (todo on every step) validates with warnings only."""
    data = _base()
    for s in data["steps"]:
        s["todo"] = "fill me"
    res = onboard_scenario.validate_file(_write(tmp_path, data))
    assert res["errors"] == []
    assert [w for w in res["warnings"] if "fill me" in w] and len(res["steps"]) == 2
    # the todo key never leaks into the validated data
    assert all("todo" not in s for s in res["data"]["steps"])


def test_writes_log_when_out_given(tmp_path: Path):
    p = _write(tmp_path, _base())
    out = tmp_path / "out"
    assert run("validate", p, "--out", out) == 0
    log = json.loads((out / "logs" / "validate.json").read_text(encoding="utf-8"))
    assert log["errors"] == [] and [s["id"] for s in log["steps"]] == ["open", "ask"]
    bad = _write(tmp_path, {"name": "x"}, "bad.json")
    assert run("validate", bad, "--out", out) == 4
    log = json.loads((out / "logs" / "validate.json").read_text(encoding="utf-8"))
    assert log["exit_code"] == 4 and log["error"].startswith("validate: INVALID")
