"""The real OpenCode binary drives the whole kit pipeline against a scripted fake LLM.

Skipped unless ``OPENCODE_BIN`` (default: the sandbox's opencode install) exists
and a Chrome/Chromium is found.  The fake model (``tests/opencode_fake_llm.py``)
answers every completion with the next kit command as a ``bash`` tool call:
doctor, dryrun, narrate-template, narrate-validate, synth (tone), record
(screencast, headless), edit, verify - then says ``SMOKE DONE``.  OpenCode runs
under a scratch ``HOME`` so the user's own config cannot interfere, with the
fake provider injected through ``OPENCODE_CONFIG_CONTENT`` (the project
``opencode.json`` and the ``demo-smoke`` agent are still loaded from the kit).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from demo_smoke import chrome
from tests.fixtures.serve import serve_dir
from tests.opencode_fake_llm import FakeOpenCodeLLM, kit_command

KIT = Path(__file__).resolve().parents[1]
APP_DIR = KIT / "tests" / "fixtures" / "app"
SCEN_DIR = KIT / "tests" / "fixtures" / "scenarios"
DEFAULT_OPENCODE = "/home/user/.opencode-bin/node_modules/.bin/opencode"
OPENCODE_BIN = os.environ.get("OPENCODE_BIN", DEFAULT_OPENCODE)
TIMEOUT_S = 600
MIN_TOOL_RESULTS = 8


def _need_opencode_and_chrome() -> None:
    if not Path(OPENCODE_BIN).is_file():
        pytest.skip(f"OpenCode binary not found: {OPENCODE_BIN} (set OPENCODE_BIN)")
    if not chrome.find_chrome():
        pytest.skip("no Chrome binary available (set DEMO_SMOKE_CHROME)")


def _write_scenario(name: str, base_url: str, dest: Path) -> Path:
    """Copy a fixture scenario with the served URL and absolute upload paths."""
    data = json.loads((SCEN_DIR / name).read_text(encoding="utf-8"))
    data["app_url"] = base_url
    for step in data["steps"]:
        for action in step.get("actions", []):
            upload = action.get("upload")
            if upload:
                upload["files"] = [str((SCEN_DIR / f).resolve()) for f in upload["files"]]
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return dest


def _opencode_env(home: Path, fake_url: str) -> dict:
    env = dict(os.environ)
    for key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME",
                "OPENCODE_CONFIG", "OPENCODE_CONFIG_DIR", "OPENCODE_PERMISSION"):
        env.pop(key, None)
    env["HOME"] = str(home)               # ~/.config/opencode, ~/.local/share/opencode -> scratch
    env["USERPROFILE"] = str(home)        # Windows homedir()
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps({
        "provider": {
            "fake": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Fake scripted LLM (test)",
                "options": {"baseURL": fake_url},
                "models": {"scripted": {"name": "scripted"}},
            }
        },
        "model": "fake/scripted",
        "small_model": "fake/scripted",   # title generation must not hit ollama
    })
    env["OPENCODE_DISABLE_MODELS_FETCH"] = "1"
    env["OPENCODE_DISABLE_AUTOUPDATE"] = "1"
    env["OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS"] = str(TIMEOUT_S * 1000)
    loopback = "127.0.0.1,localhost"
    for key in ("NO_PROXY", "no_proxy"):
        env[key] = loopback + ("," + env[key] if env.get(key) else "")
    found = chrome.find_chrome()
    if found and "DEMO_SMOKE_CHROME" not in env:
        env["DEMO_SMOKE_CHROME"] = found
    env["PYTHONPATH"] = str(KIT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def _events(stdout: str) -> list[dict]:
    out = []
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def test_opencode_runs_the_pipeline_with_a_scripted_model(tmp_path):
    _need_opencode_and_chrome()
    out = tmp_path / "out"
    home = tmp_path / "home"
    home.mkdir()
    with serve_dir(APP_DIR) as base:
        scenario = _write_scenario("fixture-pass.json", base, tmp_path / "scenario" / "pass.json")
        commands = [
            kit_command("doctor", "--out", str(out)),
            kit_command("dryrun", str(scenario), "--out", str(out), "--headless"),
            kit_command("narrate-template", str(scenario), "--out", str(out)),
            kit_command("narrate-validate", str(scenario), "--out", str(out)),
            kit_command("synth", "--out", str(out), "--tts", "tone"),
            kit_command("record", str(scenario), "--out", str(out), "--capture", "screencast", "--headless"),
            kit_command("edit", "--out", str(out)),
            kit_command("verify", "--out", str(out)),
        ]
        with FakeOpenCodeLLM(commands, log_path=tmp_path / "fake-llm.jsonl") as fake:
            argv = [OPENCODE_BIN, "run", "--agent", "demo-smoke", "--auto", "--model", "fake/scripted",
                    "--dir", str(KIT), "--format", "json", "Run the smoke pipeline as scripted"]
            t0 = time.monotonic()
            proc = subprocess.run(argv, cwd=str(KIT), env=_opencode_env(home, fake.base_url),
                                  capture_output=True, text=True, timeout=TIMEOUT_S, check=False)
            seconds = time.monotonic() - t0
            log_tail = fake.log_path.read_text(encoding="utf-8")[-3000:] if fake.log_path.is_file() else ""
            detail = (f"exit={proc.returncode} seconds={seconds:.0f}\n--- stdout (tail) ---\n{proc.stdout[-6000:]}"
                      f"\n--- stderr (tail) ---\n{proc.stderr[-4000:]}\n--- fake log (tail) ---\n{log_tail}")

            # the fake saw the whole conversation: one tool result per scripted command
            assert fake.errors == [], detail
            assert fake.max_tool_results >= MIN_TOOL_RESULTS, detail
            assert fake.tool_calls_issued == commands, detail
            assert proc.returncode == 0, detail

    events = _events(proc.stdout)
    tools = [e for e in events if e.get("type") == "tool_use"]
    inputs = [t["part"]["state"]["input"]["command"] for t in tools]
    assert inputs == commands, detail
    for t in tools:
        state = t["part"]["state"]
        assert state["status"] == "completed", detail
        assert state["metadata"].get("exit") == 0, (t["part"]["state"]["input"], state.get("output"), detail)
    texts = [e["part"]["text"] for e in events if e.get("type") == "text"]
    assert any("SMOKE DONE" in t for t in texts), detail

    # every command matched an explicit allow rule, or was auto-approved: none was denied
    outputs = "\n".join(t["part"]["state"].get("output", "") for t in tools)
    assert "denied" not in outputs.lower() and "permission" not in outputs.lower(), detail

    # the pipeline's own artifacts
    video = out / "final" / "fixture-pass.mp4"
    assert video.is_file() and video.stat().st_size > 10_000, detail
    verify = json.loads((out / "logs" / "verify.json").read_text(encoding="utf-8"))
    assert verify["pass"] is True, verify
    assert [c for c in verify["checks"] if not c["pass"]] == []
    for log in ("doctor", "dryrun", "narrate-template", "narrate-validate", "synth", "record", "markers"):
        assert (out / "logs" / f"{log}.json").is_file(), log
    dry = json.loads((out / "logs" / "dryrun.json").read_text(encoding="utf-8"))
    assert dry["verdict"] == "PASS"
    narr = json.loads((out / "audio" / "narration.json").read_text(encoding="utf-8"))
    assert [s["id"] for s in narr["steps"]] == ["open", "upload", "ask"]
    for pct in (10, 50, 90):
        assert (out / "final" / f"thumb-{pct}.png").is_file()
    # nothing leaked into the user's real config: OpenCode wrote only under the scratch HOME
    assert (home / ".local" / "share" / "opencode").is_dir(), detail
    assert seconds < TIMEOUT_S


def test_fake_llm_serves_both_wire_formats(tmp_path):
    """The fake alone: JSON and SSE replies, tool-result counting, title requests, loop guard."""
    import urllib.request

    bash_tool = {"type": "function", "function": {
        "name": "bash", "description": "run", "parameters": {
            "type": "object", "properties": {"command": {"type": "string"}, "timeout": {"type": "integer"},
                                             "description": {"type": "string"}},
            "required": ["command"]}}}
    with FakeOpenCodeLLM(["echo one", "echo two"], log_path=tmp_path / "log.jsonl") as fake:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def post(body: dict) -> bytes:
            req = urllib.request.Request(fake.base_url + "/chat/completions", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            with opener.open(req, timeout=10) as resp:
                return resp.read()

        with opener.open(fake.base_url + "/models", timeout=10) as resp:
            assert json.loads(resp.read())["data"][0]["id"] == "scripted"

        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "go"}]
        first = json.loads(post({"model": "scripted", "messages": msgs, "tools": [bash_tool], "stream": False}))
        call = first["choices"][0]["message"]["tool_calls"][0]
        assert first["choices"][0]["finish_reason"] == "tool_calls"
        args = json.loads(call["function"]["arguments"])
        assert args["command"] == "echo one" and args["timeout"] == 600000 and "description" in args

        msgs += [first["choices"][0]["message"], {"role": "tool", "tool_call_id": call["id"], "content": "one"}]
        sse = post({"model": "scripted", "messages": msgs, "tools": [bash_tool], "stream": True}).decode()
        chunks = [json.loads(line[6:]) for line in sse.splitlines() if line.startswith("data: {")]
        assert sse.rstrip().endswith("data: [DONE]")
        deltas = [c["choices"][0]["delta"] for c in chunks if c["choices"]]
        pieces = "".join(tc["function"].get("arguments", "") for d in deltas for tc in d.get("tool_calls", []))
        assert json.loads(pieces)["command"] == "echo two"
        assert [c["choices"][0]["finish_reason"] for c in chunks if c["choices"]][-1] == "tool_calls"
        assert chunks[-1]["usage"]["total_tokens"] == 120

        msgs += [{"role": "assistant", "content": None, "tool_calls": [call]},
                 {"role": "tool", "tool_call_id": call["id"], "content": "two"}]
        done = json.loads(post({"model": "scripted", "messages": msgs, "tools": [bash_tool], "stream": False}))
        assert done["choices"][0]["message"]["content"] == "SMOKE DONE"
        assert done["choices"][0]["finish_reason"] == "stop"

        title = json.loads(post({"model": "scripted", "messages": msgs[:2], "stream": False}))
        assert title["choices"][0]["message"]["content"] and "SMOKE DONE" not in title["choices"][0]["message"]["content"]

        # a client that keeps asking for the same step without a tool result is cut off
        for _ in range(3):
            post({"model": "scripted", "messages": msgs[:2], "tools": [bash_tool], "stream": False})
        stuck = json.loads(post({"model": "scripted", "messages": msgs[:2], "tools": [bash_tool], "stream": False}))
        assert "aborted" in stuck["choices"][0]["message"]["content"]
        assert fake.errors and fake.max_tool_results == 2
        assert fake.tool_calls_issued[:2] == ["echo one", "echo two"]
        lines = (tmp_path / "log.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == len(fake.requests) == 8
        assert all(json.loads(line)["body"] for line in lines)


def test_kit_command_quoting():
    cmd = kit_command("dryrun", "/tmp/a b/x.json", "--out", "/tmp/o", python="/venv/bin/python")
    assert cmd.startswith("/venv/bin/python -m demo_smoke dryrun ")
    if os.name != "nt":
        assert "'/tmp/a b/x.json'" in cmd
    else:
        assert '"/tmp/a b/x.json"' in cmd
    assert kit_command("doctor").split()[0] == sys.executable
