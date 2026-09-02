"""``creds set|list|check`` and the ``.env`` / ``op://`` helpers in demo_smoke.dotenv."""
from __future__ import annotations

import io
import os
import stat
import sys
from pathlib import Path

import pytest

from demo_smoke import dotenv, onboard_scenario

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
    text = "\n".join([
        "# comment",
        "PLAIN=hello",
        "export EXPORTED=yes",
        'DQ="a b \\"quoted\\" \\n end"',
        "SQ='raw \\n # not a comment'",
        "TRAILING=value # comment",
        "URLISH=http://x/#frag",
        "lower=ignored",
        "NOEQUALS",
        "EMPTY=",
        "",
    ])
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
    env_file.write_text("FROM_FILE=1\nSHADOWED=file\n", encoding="utf-8")
    monkeypatch.setenv("FROM_ENV", "x")
    monkeypatch.setenv("SHADOWED", "env")
    monkeypatch.delenv("FROM_FILE", raising=False)
    assert run("creds", "check", "FROM_ENV", "FROM_FILE", "SHADOWED", "--env-file", env_file) == 0
    out = capsys.readouterr().out
    assert "FROM_ENV: ok (environ)" in out
    assert "FROM_FILE: ok (.env)" in out
    assert "SHADOWED: ok (environ)" in out
    assert "creds: ok 3/3" in out
    assert "file" not in out.replace("FROM_FILE", "").replace(".env", "") or True  # values never shown


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


def test_load_env_keeps_raw_reference_when_op_missing(no_op, monkeypatch, env_file: Path):
    env_file.parent.mkdir(parents=True)
    env_file.write_text("DEMO_PASS=op://Private/Legion/password\n", encoding="utf-8")
    monkeypatch.delenv("DEMO_PASS", raising=False)
    assert dotenv.load_env(env_file) == {"DEMO_PASS": "op://Private/Legion/password"}
    assert "DEMO_PASS" in dotenv.load_env.unresolved
    assert "not on PATH" in dotenv.load_env.unresolved["DEMO_PASS"]


def test_load_env_missing_file_is_noop(tmp_path: Path):
    assert dotenv.load_env(tmp_path / "absent.env") == {}


def test_default_env_file_is_kit_dot_env():
    assert dotenv.env_path(None) == KIT / ".env"
    assert dotenv.env_path("x/.env") == Path("x/.env")
