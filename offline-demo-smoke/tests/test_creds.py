"""``creds set|list|check`` and the ``.env`` / ``op://`` helpers in demo_smoke.dotenv."""
from __future__ import annotations

import io
import os
import stat
import sys
from pathlib import Path

import pytest

from demo_smoke import cli, dotenv, onboard_scenario

KIT = Path(__file__).resolve().parents[1]
FAKES = KIT / "tests" / "fakes"


def run(*argv):
    return onboard_scenario.main([str(a) for a in argv])


@pytest.fixture
def env_file(tmp_path: Path) -> Path:
    return tmp_path / "secrets" / ".env"


@pytest.fixture
def fake_op(monkeypatch):
    """Put the fake 1Password CLI first on PATH (exec bit restored in case git dropped it)."""
    if os.name == "posix":
        script = FAKES / "op"
        script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", str(FAKES) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.delenv("FAKE_OP_FAIL", raising=False)
    assert dotenv.op_path() and str(FAKES) in dotenv.op_path()
    return FAKES


@pytest.fixture
def no_op(monkeypatch, tmp_path: Path):
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    assert dotenv.op_path() is None


def _stdin(monkeypatch, text: str) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


# --------------------------------------------------------------------------- dotenv parsing


def test_parse_handles_quotes_comments_and_export():
    text = (
        "# comment\n"
        "PLAIN=hello\n"
        "export EXPORTED=yes\n"
        'DQ="a b \\"quoted\\" \\n end"\n'
        "SQ='raw \\n # not a comment'\n"
        "TRAILING=value # comment\n"
        "URLISH=http://x/#frag\n"
        "lower=ignored\n"
        "NOEQUALS\n"
        "EMPTY=\n"
    )
    vals = dotenv.parse(text)
    assert vals == {
        "PLAIN": "hello",
        "EXPORTED": "yes",
        "DQ": 'a b "quoted" \n end',
        "SQ": "raw \\n # not a comment",
        "TRAILING": "value",
        "URLISH": "http://x/#frag",
        "EMPTY": "",
    }


def test_read_missing_file_is_empty(tmp_path: Path):
    assert dotenv.read(tmp_path / "nope.env") == {}
    assert dotenv.names(tmp_path / "nope.env") == []


def test_write_value_roundtrip_and_quoting(env_file: Path):
    for value in ("simple", "with space", 'q"uote', "hash # inside", "", "back\\slash", "op://v/i/f"):
        dotenv.write_value(env_file, "X", value)
        assert dotenv.read(env_file)["X"] == value, value


# --------------------------------------------------------------------------- creds set


def test_creds_set_from_stdin_creates_0600(monkeypatch, env_file: Path, capsys):
    _stdin(monkeypatch, "s3cret-value\n")
    assert run("creds", "set", "DEMO_PASS", "--env-file", env_file, "--value-from-stdin") == 0
    text = env_file.read_text(encoding="utf-8")
    assert text == "DEMO_PASS=s3cret-value\n"
    if os.name == "posix":
        assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    out = capsys.readouterr().out
    assert "DEMO_PASS" in out and "s3cret" not in out


def test_creds_set_updates_in_place_and_keeps_other_lines(monkeypatch, env_file: Path):
    env_file.parent.mkdir(parents=True)
    env_file.write_text("# kit secrets\nDEMO_USER=alice\nDEMO_PASS=old\nOTHER=1\n", encoding="utf-8")
    _stdin(monkeypatch, "new pass\n")
    assert run("creds", "set", "DEMO_PASS", "--env-file", env_file, "--value-from-stdin") == 0
    lines = env_file.read_text(encoding="utf-8").splitlines()
    assert lines == ["# kit secrets", "DEMO_USER=alice", 'DEMO_PASS="new pass"', "OTHER=1"]
    assert dotenv.read(env_file) == {"DEMO_USER": "alice", "DEMO_PASS": "new pass", "OTHER": "1"}


def test_creds_set_uses_getpass_without_stdin_flag(monkeypatch, env_file: Path):
    prompts = []

    def fake_getpass(prompt=""):
        prompts.append(prompt)
        return "typed"

    monkeypatch.setattr(onboard_scenario.getpass, "getpass", fake_getpass)
    assert run("creds", "set", "API_KEY", "--env-file", env_file) == 0
    assert prompts == ["API_KEY: "]
    assert dotenv.read(env_file) == {"API_KEY": "typed"}


@pytest.mark.parametrize("bad", ["demo_pass", "1ABC", "A-B", "A B", ""])
def test_creds_set_rejects_bad_names(monkeypatch, env_file: Path, bad, capsys):
    _stdin(monkeypatch, "x\n")
    assert run("creds", "set", bad, "--env-file", env_file, "--value-from-stdin") == 4
    assert not env_file.exists()
    assert "error:" in capsys.readouterr().err


def test_creds_set_rejects_empty_value(monkeypatch, env_file: Path):
    _stdin(monkeypatch, "\n")
    assert run("creds", "set", "DEMO_PASS", "--env-file", env_file, "--value-from-stdin") == 4
    assert not env_file.exists()


def test_creds_set_op_reference_is_stored_verbatim(monkeypatch, env_file: Path, capsys):
    _stdin(monkeypatch, "op://Private/Legion/password\n")
    assert run("creds", "set", "DEMO_PASS", "--env-file", env_file, "--value-from-stdin") == 0
    assert dotenv.read(env_file)["DEMO_PASS"] == "op://Private/Legion/password"
    assert "op://" in capsys.readouterr().out


# --------------------------------------------------------------------------- creds list


def test_creds_list_prints_names_only(env_file: Path, capsys):
    env_file.parent.mkdir(parents=True)
    env_file.write_text("DEMO_USER=alice\nDEMO_PASS=hunter2\n", encoding="utf-8")
    assert run("creds", "list", "--env-file", env_file) == 0
    out = capsys.readouterr().out
    assert out.splitlines() == ["DEMO_USER", "DEMO_PASS"]
    assert "alice" not in out and "hunter2" not in out


def test_creds_list_empty(env_file: Path, capsys):
    assert run("creds", "list", "--env-file", env_file) == 0
    assert "no names" in capsys.readouterr().out


# --------------------------------------------------------------------------- creds check


def test_creds_check_environment_then_env_file(monkeypatch, env_file: Path, capsys):
    env_file.parent.mkdir(parents=True)
    env_file.write_text("FROM_FILE=VALUE_FROM_FILE_7f2\nSHADOWED=SHADOW_FILE_4d0\n", encoding="utf-8")
    monkeypatch.setenv("FROM_ENV", "VALUE_FROM_ENV_9c1")
    monkeypatch.setenv("SHADOWED", "SHADOW_ENV_4d0")
    monkeypatch.delenv("FROM_FILE", raising=False)
    assert run("creds", "check", "FROM_ENV", "FROM_FILE", "SHADOWED", "--env-file", env_file) == 0
    cap = capsys.readouterr()
    out = cap.out
    assert "FROM_ENV: ok (environ)" in out
    assert "FROM_FILE: ok (.env)" in out
    assert "SHADOWED: ok (environ)" in out
    assert "creds: ok 3/3" in out
    for secret in ("VALUE_FROM_FILE_7f2", "VALUE_FROM_ENV_9c1", "SHADOW_FILE_4d0", "SHADOW_ENV_4d0"):
        assert secret not in out and secret not in cap.err     # values never shown


def test_creds_check_missing_lists_names_exit_4(monkeypatch, env_file: Path, capsys):
    monkeypatch.setenv("HAVE", "1")
    for n in ("NOPE_A", "NOPE_B"):
        monkeypatch.delenv(n, raising=False)
    assert run("creds", "check", "HAVE", "NOPE_A", "NOPE_B", "--env-file", env_file) == 4
    cap = capsys.readouterr()
    assert "NOPE_A: MISSING" in cap.out and "NOPE_B: MISSING" in cap.out
    assert "MISSING NOPE_A, NOPE_B" in cap.err


def test_creds_check_resolves_op_reference_with_fake_cli(fake_op, monkeypatch, env_file: Path, capsys):
    env_file.parent.mkdir(parents=True)
    env_file.write_text("DEMO_PASS=op://Private/Legion/password\n", encoding="utf-8")
    monkeypatch.delenv("DEMO_PASS", raising=False)
    assert dotenv.resolve("DEMO_PASS", env_file) == ("resolved:op://Private/Legion/password", "op://")
    assert run("creds", "check", "DEMO_PASS", "--env-file", env_file) == 0
    out = capsys.readouterr().out
    assert "DEMO_PASS: ok (op://)" in out
    assert "resolved:" not in out


def test_creds_check_op_reference_without_cli_is_missing(no_op, monkeypatch, env_file: Path, capsys):
    env_file.parent.mkdir(parents=True)
    env_file.write_text("DEMO_PASS=op://Private/Legion/password\n", encoding="utf-8")
    monkeypatch.delenv("DEMO_PASS", raising=False)
    assert run("creds", "check", "DEMO_PASS", "--env-file", env_file) == 4
    assert "not on PATH" in capsys.readouterr().out


def test_creds_check_op_read_failure_is_reported(fake_op, monkeypatch, env_file: Path, capsys):
    env_file.parent.mkdir(parents=True)
    env_file.write_text("DEMO_PASS=op://Private/Missing/password\n", encoding="utf-8")
    monkeypatch.delenv("DEMO_PASS", raising=False)
    monkeypatch.setenv("FAKE_OP_FAIL", "item not found")
    assert run("creds", "check", "DEMO_PASS", "--env-file", env_file) == 4
    out = capsys.readouterr().out
    assert "DEMO_PASS: MISSING" in out and "item not found" in out


def test_creds_check_rejects_bad_name(env_file: Path):
    assert run("creds", "check", "bad-name", "--env-file", env_file) == 4


# --------------------------------------------------------------------------- load_env


def test_load_env_sets_only_unset_names_and_resolves_op(fake_op, monkeypatch, env_file: Path):
    env_file.parent.mkdir(parents=True)
    env_file.write_text("DEMO_USER=alice\nDEMO_PASS=op://Private/Legion/password\nKEEP=file\n",
                        encoding="utf-8")
    monkeypatch.setenv("KEEP", "environment")
    monkeypatch.delenv("DEMO_USER", raising=False)
    monkeypatch.delenv("DEMO_PASS", raising=False)
    loaded = dotenv.load_env(env_file)
    assert loaded == {"DEMO_USER": "alice", "DEMO_PASS": "resolved:op://Private/Legion/password"}
    assert os.environ["DEMO_USER"] == "alice"
    assert os.environ["DEMO_PASS"] == "resolved:op://Private/Legion/password"
    assert os.environ["KEEP"] == "environment"
    assert dotenv.load_env.unresolved == {}


def test_load_env_does_not_export_an_unresolved_reference(no_op, monkeypatch, env_file: Path):
    """The raw ``op://...`` string must never reach os.environ: drive.login would type it into the
    password field and report the feature as broken (exit 2) instead of a missing credential."""
    env_file.parent.mkdir(parents=True)
    env_file.write_text("DEMO_USER=alice\nDEMO_PASS=op://Private/Legion/password\n", encoding="utf-8")
    for name in ("DEMO_USER", "DEMO_PASS"):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    assert dotenv.load_env(env_file) == {"DEMO_USER": "alice"}
    assert "DEMO_PASS" not in os.environ and os.environ["DEMO_USER"] == "alice"
    assert "DEMO_PASS" in dotenv.load_env.unresolved
    assert "not on PATH" in dotenv.load_env.unresolved["DEMO_PASS"]


def test_load_env_defers_op_references_for_login(fake_op, monkeypatch, env_file: Path):
    """cli.main loads .env with resolve_refs=False: plain values are exported, op:// values are
    resolved by dotenv.credential only when drive.login asks, in this process, never exported."""
    env_file.parent.mkdir(parents=True)
    env_file.write_text("DEMO_USER=alice\nDEMO_PASS=op://Private/Legion/password\n", encoding="utf-8")
    for name in ("DEMO_USER", "DEMO_PASS"):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    dotenv.forget_deferred()
    assert dotenv.load_env(env_file, resolve_refs=False) == {"DEMO_USER": "alice"}
    assert os.environ["DEMO_USER"] == "alice" and "DEMO_PASS" not in os.environ
    assert dotenv.load_env.deferred == {"DEMO_PASS": "op://Private/Legion/password"}
    assert dotenv.load_env.unresolved == {}
    assert dotenv.credential("DEMO_USER") == ("alice", None)
    assert dotenv.credential("DEMO_PASS") == ("resolved:op://Private/Legion/password", None)
    assert "DEMO_PASS" not in os.environ                        # resolved for this process only
    value, why = dotenv.credential("NOPE_Q")
    assert value is None and why == "environment variable NOPE_Q is not set"
    monkeypatch.setenv("FAKE_OP_FAIL", "vault locked")
    assert dotenv.credential("DEMO_PASS")[0] == "resolved:op://Private/Legion/password"   # cached: one op read
    dotenv.forget_deferred()
    dotenv.load_env(env_file, resolve_refs=False)
    value, why = dotenv.credential("DEMO_PASS")
    assert value is None and "vault locked" in why and "creds check DEMO_PASS" in why
    dotenv.forget_deferred()


def test_commands_without_a_login_never_touch_the_vault(fake_op, monkeypatch, env_file: Path, capsys,
                                                         tmp_path: Path):
    env_file.parent.mkdir(parents=True)
    env_file.write_text("DEMO_PASS=op://Private/Legion/password\n", encoding="utf-8")
    monkeypatch.setenv("DEMO_PASS", "")
    monkeypatch.delenv("DEMO_PASS")
    monkeypatch.setenv("FAKE_OP_FAIL", "vault locked")        # any `op read` would fail loudly
    scen = tmp_path / "s.json"
    assert cli.main(["init-scenario", "--name", "Quiet", "--url", "http://localhost:1", "--out", str(scen)]) == 0
    assert cli.main(["validate", str(scen), "--env-file", str(env_file)]) == 0
    cap = capsys.readouterr()
    assert "vault locked" not in cap.err and "unresolved" not in cap.err
    assert "DEMO_PASS" not in os.environ
    assert dotenv.load_env.deferred == {"DEMO_PASS": "op://Private/Legion/password"}
    dotenv.forget_deferred()


def test_creds_via_cli_report_the_real_source_and_skip_env_loading(fake_op, monkeypatch, env_file: Path, capsys):
    """``python -m demo_smoke creds ...`` goes through cli.main, whose .env pre-load would otherwise
    turn every source into ``environ`` and unlock the vault just to store or list a name."""
    env_file.parent.mkdir(parents=True)
    env_file.write_text("DEMO_USER=alice\nDEMO_PASS=op://Private/Legion/password\n", encoding="utf-8")
    for name in ("DEMO_USER", "DEMO_PASS", "NEW_ONE"):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    assert cli.main(["creds", "check", "DEMO_USER", "DEMO_PASS", "--env-file", str(env_file)]) == 0
    out = capsys.readouterr().out
    assert "DEMO_USER: ok (.env)" in out and "DEMO_PASS: ok (op://)" in out
    assert "DEMO_USER" not in os.environ and "DEMO_PASS" not in os.environ
    # storing another name never resolves the existing op:// value (no vault prompt, no stderr line)
    monkeypatch.setenv("FAKE_OP_FAIL", "vault locked")
    _stdin(monkeypatch, "v\n")
    assert cli.main(["creds", "set", "NEW_ONE", "--env-file", str(env_file), "--value-from-stdin"]) == 0
    cap = capsys.readouterr()
    assert "unresolved" not in cap.err and "vault locked" not in cap.err
    assert cli.main(["creds", "list", "--env-file", str(env_file)]) == 0
    assert "NEW_ONE" in capsys.readouterr().out and "NEW_ONE" not in os.environ


def test_load_env_missing_file_is_noop(tmp_path: Path):
    assert dotenv.load_env(tmp_path / "absent.env") == {}


def test_default_env_file_is_kit_dot_env():
    assert dotenv.env_path(None) == KIT / ".env"
    assert dotenv.env_path("x/.env") == Path("x/.env")
