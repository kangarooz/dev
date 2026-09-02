"""``demo_smoke.opencode_events`` on the captured sample and on hostile synthetic input.

The sample (``tests/fixtures/opencode-events.sample.jsonl``) is the real stdout of
``opencode run --agent demo-smoke --auto --format json`` driven by the scripted fake
LLM (see ``tests/opencode_capture_events.py``), scrubbed of paths and ids.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pytest

from demo_smoke import opencode_events as oe
from tests import opencode_capture_events as cap

KIT = Path(__file__).resolve().parents[1]
SAMPLE = KIT / "tests" / "fixtures" / "opencode-events.sample.jsonl"
GOLDEN = ["doctor", "dryrun", "narrate-template", "narrate-validate", "synth", "record", "edit", "verify"]


def _ev(etype: str, **part) -> str:
    return json.dumps({"type": etype, "timestamp": 1700000000000, "sessionID": "ses_test", "part": part})


def _tool(command: str, status: str = "completed", exit_code: int | None = 0, error: str | None = None,
          start: int = 1700000000000, end: int = 1700000001000, tool: str = "bash", **extra_input) -> str:
    state = {"status": status, "input": {"command": command, **extra_input}, "output": "out",
             "time": {"start": start, "end": end}, "title": command}
    if exit_code is not None:
        state["metadata"] = {"exit": exit_code, "output": "out", "truncated": False}
    if error is not None:
        state["error"] = error
    return _ev("tool_use", type="tool", tool=tool, callID="call_1", state=state)


def _finish(reason: str = "tool-calls", tokens: dict | None = None, cost=0) -> str:
    part = {"type": "step-finish", "reason": reason, "cost": cost}
    part["tokens"] = tokens if tokens is not None else {
        "total": 120, "input": 100, "output": 20, "reasoning": 0, "cache": {"write": 0, "read": 0}}
    return _ev("step_finish", **part)


# ----------------------------------------------------------------------------- the real sample


@pytest.fixture(scope="module")
def sample() -> dict:
    assert SAMPLE.is_file(), "capture it with: python -m tests.opencode_capture_events"
    return oe.parse_file(SAMPLE)


def test_sample_is_one_json_object_per_line():
    lines = SAMPLE.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 20
    for ln in lines:
        e = json.loads(ln)
        assert isinstance(e, dict) and {"type", "timestamp", "sessionID", "part"} <= set(e)


def test_sample_is_scrubbed():
    text = SAMPLE.read_text(encoding="utf-8")
    assert "/home/" not in text and "/tmp/" not in text and "C:\\\\" not in text
    assert "/Users/" not in text and "<tmp>" in text and "<python>" in text
    # real OpenCode ids are long random strings; the fixture carries numbered ones only
    assert not re.search(r"\b(ses|msg|prt)_[A-Za-z0-9]{10,}", text)
    assert re.search(r"\bses_0001\b", text)


def test_sample_tool_calls_are_the_golden_path(sample):
    calls = sample["tool_calls"]
    assert [c["kit_command"] for c in calls] == GOLDEN
    assert [c["name"] for c in calls] == ["bash"] * 8
    for i, c in enumerate(calls):
        assert c["index"] == i
        assert c["command"].startswith("<python> -m demo_smoke ")
        assert c["status"] == "completed" and c["exit_code"] == 0 and c["denied"] is False
        assert isinstance(c["started"], int) and isinstance(c["finished"], int)
        assert c["finished"] >= c["started"] and c["seconds"] is not None and c["seconds"] >= 0
        assert c["call_id"] and c["title"] and c["output"] and c["error"] is None
        assert c["truncated"] is False and c["input"]["timeout"] == 600000
    assert calls[-1]["output"].startswith("verify: PASS")
    # the recording is the long one; timestamps are increasing across calls
    assert max(calls, key=lambda c: c["seconds"])["kit_command"] == "record"
    assert [c["started"] for c in calls] == sorted(c["started"] for c in calls)


def test_sample_text_session_status_and_counts(sample):
    assert sample["assistant_text"] == ["SMOKE DONE"]
    assert sample["reasoning"] == []
    assert sample["session_id"] == "ses_0001"
    assert sample["final_status"] == "completed"
    assert sample["permissions"] == [] and sample["denied"] == [] and sample["errors"] == []
    assert sample["step_limit_reached"] is False
    assert sample["steps"] == 9                     # 8 tool steps + the closing text step
    assert sample["unknown_event_types"] == []
    assert sample["event_counts"] == {"step_start": 9, "tool_use": 8, "step_finish": 9, "text": 1}
    assert sample["lines"] == sample["json_lines"] == 27 and sample["non_json_lines"] == 0
    assert sample["malformed_events"] == 0
    assert sample["first_timestamp"] < sample["last_timestamp"]


def test_sample_usage_is_summed_over_steps(sample):
    u = sample["usage"]
    assert u == {"total": 1080, "input": 900, "output": 180, "reasoning": 0,
                 "cache_write": 0, "cache_read": 0, "cost": 0, "steps": 9}


def test_sample_summary_and_stage_times(sample):
    s = oe.summary(sample)
    assert s["tool_calls"] == 8 and s["kit_commands"] == GOLDEN
    assert len(s["commands"]) == 8 and all("-m demo_smoke" in c for c in s["commands"])
    assert s["failed_tool_calls"] == [] and s["errors"] == []
    assert s["assistant_messages"] == 1 and s["permission_prompts"] == 0 and s["denied"] == 0
    assert s["steps"] == 9 and s["step_limit_reached"] is False
    assert s["tokens_in"] == 900 and s["tokens_out"] == 180 and s["tokens_total"] == 1080 and s["cost"] == 0
    assert s["wall_s"] and s["wall_s"] > 10
    stages = s["stages"]
    assert list(stages) == list(oe.STAGES)
    assert all(v is not None and v > 0 for v in stages.values()), stages
    assert stages["record"] > stages["doctor"]
    assert s["used_narrate_template"] is True and s["used_narrate_llm"] is False
    assert s["narration_written_by_agent"] is False
    json.dumps(s)   # report-safe


def test_sample_parses_the_same_from_text_bytes_lines_and_path(sample):
    text = SAMPLE.read_text(encoding="utf-8")
    assert oe.parse(text) == sample
    assert oe.parse(text.encode("utf-8")) == sample
    assert oe.parse(text.splitlines()) == sample
    assert oe.parse([ln.encode("utf-8") for ln in text.splitlines()]) == sample
    assert oe.parse(SAMPLE) == sample


# ----------------------------------------------------------------------------- tolerance


def test_empty_and_none_input():
    for empty in ("", [], b"", None, "\n\n   \n"):
        r = oe.parse(empty)
        assert r["final_status"] == "empty"
        assert r["tool_calls"] == [] and r["assistant_text"] == [] and r["usage"] is None
        assert r["session_id"] is None and r["event_counts"] == {} and r["unknown_event_types"] == []
        assert r["lines"] == 0
    assert oe.parse_file(Path("/nonexistent/opencode.jsonl"))["final_status"] == "empty"
    assert oe.summary(oe.parse(""))["tool_calls"] == 0


def test_non_json_and_malformed_lines_never_raise():
    lines = [
        "plain progress text",
        "{not json",
        '{"type": "tool_use", "part": "junk"}',                 # part is not a dict
        '{"type": "tool_use"}',                                  # no part at all
        '{"type": "text", "part": {"text": 42}}',                # wrong text type
        '{"type": "text", "part": {"text": "   "}}',             # blank text
        '{"no": "type"}',
        '{"type": ""}',
        '{"type": 7}',
        "[1, 2, \"three\"]",
        '"a json string"',
        "123",
        '{"type": "step_finish", "part": {"tokens": "lots", "cost": {"usd": 1}}}',
        '{"type": "step_finish", "part": {"reason": "stop", "tokens": {"input": "12", "output": "3"}}}',
        '{"type": "custom.thing", "timestamp": "soon"}',
        '{"type": "custom.thing"}',
        '{"type": "another", "timestamp": 5, "sessionID": "ses_x"}',
    ]
    r = oe.parse(lines)
    assert r["non_json_lines"] == 4        # 2 plain + 2 scalar JSON that are not object/array-prefixed
    assert r["json_lines"] == 13
    assert r["unknown_event_types"] == ["<missing>", "custom.thing", "another"]
    assert r["event_counts"]["<missing>"] == 3 and r["event_counts"]["custom.thing"] == 2
    assert r["event_counts"]["<non-object>"] == 3
    assert r["malformed_events"] == 3
    assert r["session_id"] == "ses_x"
    assert r["assistant_text"] == []
    assert len(r["tool_calls"]) == 2
    junk, bare = r["tool_calls"]
    assert junk["name"] == "unknown" and junk["command"] is None and junk["status"] == "unknown"
    assert junk["started"] is None and junk["seconds"] is None and "exit_code" not in junk
    assert bare["input"] == {}
    assert r["usage"] == {"input": 12, "output": 3, "total": 15, "steps": 1}
    assert r["final_status"] == "completed"
    json.dumps(r)


def test_json_array_lines_are_events_too():
    a = json.loads(_ev("text", text="hello"))
    b = json.loads(_tool("ls"))
    r = oe.parse(json.dumps([a, b]))
    assert r["assistant_text"] == ["hello"] and [c["command"] for c in r["tool_calls"]] == ["ls"]
    assert r["json_lines"] == 1 and r["event_counts"] == {"text": 1, "tool_use": 1}


def test_tool_input_variants():
    lines = [
        # arguments as a JSON string (some providers), and camel-cased exit code
        json.dumps({"type": "tool_use", "part": {"tool": "bash", "state": {
            "status": "completed", "input": json.dumps({"command": "echo hi"}), "metadata": {"exitCode": "2"},
            "time": {"start": "1000", "end": "3500"}}}}),
        # a non-bash tool: no command, but a file path
        json.dumps({"type": "tool_use", "part": {"tool": "write", "state": {
            "status": "completed", "input": {"filePath": "demo-output/audio/narration.json", "content": "{}"}}}}),
        # unparsable string input
        json.dumps({"type": "tool_use", "part": {"tool": "bash", "state": {"input": "not json"}}}),
        # output only inside metadata, no state.output
        json.dumps({"type": "tool_use", "part": {"tool": "bash", "state": {
            "status": "completed", "input": {"command": "x"}, "metadata": {"output": "meta out", "exit": 0}}}}),
    ]
    r = oe.parse(lines)
    a, b, c, d = r["tool_calls"]
    assert a["command"] == "echo hi" and a["exit_code"] == 2 and a["seconds"] == 2.5
    assert a["started"] == 1000 and a["finished"] == 3500
    assert b["name"] == "write" and b["command"] is None and b["kit_command"] is None
    assert c["input"] == {"raw": "not json"} and c["command"] is None
    assert d["output"] == "meta out"
    assert oe.wrote_narration(r["tool_calls"]) is True
    assert r["final_status"] == "incomplete"      # tool calls but no step_finish "stop"


def test_permission_lines_and_denied_tool_calls():
    lines = [
        _ev("step_start"),
        "\x1b[1m!\x1b[0m permission requested: bash (rm -rf *, git push*); auto-rejecting",
        "permission requested: edit (scenarios/x.json)",
        _tool("rm -rf /", status="error", exit_code=None,
              error="The user rejected permission to use this specific tool call."),
        _tool("python -m demo_smoke doctor --out o"),
        _finish("stop"),
    ]
    r = oe.parse(lines)
    assert r["non_json_lines"] == 2
    assert [p["permission"] for p in r["permissions"]] == ["bash", "edit"]
    assert r["permissions"][0]["patterns"] == ["rm -rf *", "git push*"] and r["permissions"][0]["denied"] is True
    assert r["permissions"][1]["patterns"] == ["scenarios/x.json"] and r["permissions"][1]["denied"] is False
    assert "\x1b" not in r["permissions"][0]["raw"]
    assert r["tool_calls"][0]["denied"] is True and r["tool_calls"][0]["status"] == "error"
    assert r["tool_calls"][1]["denied"] is False
    kinds = [(d["kind"], d.get("command") or d.get("permission")) for d in r["denied"]]
    assert kinds == [("tool", "rm -rf /"), ("permission", "bash")]
    s = oe.summary(r)
    assert s["permission_prompts"] == 2 and s["denied"] == 2
    assert s["failed_tool_calls"] == [{"name": "bash", "command": "rm -rf /", "exit_code": None,
                                       "error": "The user rejected permission to use this specific tool call."}]
    assert r["final_status"] == "completed"


def test_error_events_and_step_limit():
    err = json.dumps({"type": "error", "timestamp": 5, "sessionID": "ses_e",
                      "error": {"name": "UnknownError", "data": {"message": "provider exploded"}}})
    r = oe.parse([_ev("step_start"), err, _finish("stop")])
    assert r["errors"] == [{"name": "UnknownError", "message": "provider exploded", "timestamp": 5}]
    assert r["final_status"] == "error"
    assert oe.parse([json.dumps({"type": "error", "error": "plain string"})])["errors"][0]["message"] == "plain string"
    assert oe.parse([json.dumps({"type": "error"})])["errors"][0]["name"] == "Error"

    limit = _ev("text", text="The maximum number of steps allowed for this task has been reached. Stopping.")
    r = oe.parse([_ev("step_start"), _tool("ls"), _finish("tool-calls"), _ev("step_start"), limit, _finish("stop")])
    assert r["step_limit_reached"] is True and r["final_status"] == "step_limit"
    assert oe.summary(r)["step_limit_reached"] is True
    # the notice may also arrive as a plain stdout line
    r = oe.parse(["The maximum number of steps allowed for this task has been reached.", _finish("stop")])
    assert r["step_limit_reached"] is True


def test_final_status_variants():
    assert oe.parse([_ev("step_start"), _tool("ls"), _finish("tool-calls")])["final_status"] == "incomplete"
    assert oe.parse([_ev("step_start"), _tool("ls"), _finish("stop")])["final_status"] == "completed"
    assert oe.parse([_ev("step_start"), _ev("text", text="hi"), _finish("length")])["final_status"] == "completed"
    assert oe.parse([_ev("text", text="hi")])["final_status"] == "completed"       # text only, no steps
    assert oe.parse([_ev("weird")])["final_status"] == "incomplete"
    assert oe.parse([_ev("reasoning", text="thinking..."), _ev("text", text="done"), _finish("stop")])["reasoning"] \
        == ["thinking..."]


def test_usage_accumulates_cost_and_partial_tokens():
    lines = [_finish("tool-calls", tokens={"input": 10, "output": 5, "cache": {"read": 7}}, cost=0.0015),
             _finish("stop", tokens={"input": 20, "output": 1, "reasoning": 4}, cost=0.0025)]
    u = oe.parse(lines)["usage"]
    assert u["input"] == 30 and u["output"] == 6 and u["reasoning"] == 4 and u["cache_read"] == 7
    assert u["total"] == 40 and u["cost"] == pytest.approx(0.004) and u["steps"] == 2
    assert "cache_write" not in u
    assert oe.parse([_finish("stop", tokens={}, cost=None)])["usage"] is None
    assert oe.parse([_finish("stop", tokens={}, cost=None), _ev("step_start")])["usage"] is None


def test_stage_seconds_sums_repeats_and_narrate_variants():
    calls = oe.parse([
        _tool("python -m demo_smoke doctor --out o", start=0, end=1000),
        _tool("python -m demo_smoke narrate-llm s.json --out o", start=1000, end=4000),
        _tool("python -m demo_smoke narrate-validate --out o", start=4000, end=4500),
        _tool("python -m demo_smoke narrate-template s.json --out o", start=4500, end=5000),
        _tool("python -m demo_smoke dryrun s.json --out o", start=5000, end=7000),
        _tool("python -m demo_smoke dryrun s.json --out o", start=7000, end=8000),       # retry: summed
        _tool("python -m demo_smoke check-model --base-url u --model m", start=8000, end=9000),  # not a stage
        _tool("ls", start=9000, end=9100),
        _tool(".venv\\Scripts\\python.exe -m demo_smoke verify --out o", start=9100, end=9600),
    ])["tool_calls"]
    stages = oe.stage_seconds(calls)
    assert stages == {"doctor": 1.0, "dryrun": 3.0, "narrate": 4.0, "synth": None, "record": None,
                      "edit": None, "verify": 0.5}
    assert [c["kit_command"] for c in calls][-3:] == ["check-model", None, "verify"]
    assert oe.kit_command_of(None) is None and oe.kit_command_of("echo -m demo_smoke") is None
    assert oe.kit_command_of("'/p y/python' -m demo_smoke record 'a b.json'") == "record"
    s = oe.summary({"tool_calls": calls})
    assert s["used_narrate_llm"] and s["used_narrate_template"]


def test_wrote_narration_detection():
    def calls(*lines):
        return oe.parse(list(lines))["tool_calls"]

    assert oe.wrote_narration([]) is False
    assert oe.wrote_narration(calls(_tool("python -m demo_smoke narrate-template s.json --out o"))) is False
    assert oe.wrote_narration(calls(_tool("cat demo-output/audio/narration.json"))) is False
    assert oe.wrote_narration(calls(_tool("printf '{}' > demo-output/audio/narration.json"))) is True
    assert oe.wrote_narration(calls(_tool("echo x", tool="edit", filePath="C:\\out\\audio\\narration.json"))) is True
    assert oe.wrote_narration(calls(_tool("echo x", tool="edit", filePath="C:\\out\\logs\\doctor.json"))) is False


# ----------------------------------------------------------------------------- CLI registration


def _parser() -> tuple[argparse.ArgumentParser, dict]:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd")
    run_map: dict = {}
    oe.register(sub, run_map)
    return p, run_map


def test_register_and_command(tmp_path, capsys):
    p, run_map = _parser()
    assert run_map == {"opencode-events": oe.cmd_opencode_events}
    args = p.parse_args(["opencode-events", str(SAMPLE), "--out", str(tmp_path / "out")])
    assert args.fn is oe.cmd_opencode_events and args.cmd == "opencode-events"
    assert args.fn(args) == 0
    line = capsys.readouterr().out.strip()
    assert line.startswith("opencode-events: completed tool_calls=8 steps=9 messages=1 permissions=0 denied=0")
    log = json.loads((tmp_path / "out" / "logs" / "opencode-events.json").read_text(encoding="utf-8"))
    assert log["summary"]["kit_commands"] == GOLDEN and log["events"]["session_id"] == "ses_0001"

    args = p.parse_args(["opencode-events", str(SAMPLE), "--json"])
    assert args.fn(args) == 0
    assert json.loads(capsys.readouterr().out)["tool_calls"] == 8
    assert not (tmp_path / "logs").exists()

    args = p.parse_args(["opencode-events", str(tmp_path / "missing.jsonl")])
    assert args.fn(args) == 4
    assert "not found" in capsys.readouterr().out


# ----------------------------------------------------------------------------- the capture helper


def test_scrub_paths_and_ids(tmp_path):
    tmp = tmp_path / "scratch"
    reps = cap.path_replacements(tmp, kit=Path("/kit/dir"), python="/venv/bin/python")
    assert reps[0][0] == max((r for r, _ in reps), key=len)      # longest first
    raw = json.dumps({
        "sessionID": "ses_f9c964ee6ffe6glb4jctqZ05Xj",
        "part": {"id": "prt_06369bed2001cSKFI8LpDmEm75", "messageID": "msg_06369b733001pOqpRBD5DHRr1Q",
                 "sessionID": "ses_f9c964ee6ffe6glb4jctqZ05Xj", "callID": "call_0_0",
                 "state": {"input": {"command": f"/venv/bin/python -m demo_smoke dryrun {tmp}/s.json --out {tmp}/out"},
                           "output": f"wrote /kit/dir/demo-output/x and {tmp}\\win\\path"}},
    })
    raw += "\n" + json.dumps({"sessionID": "ses_f9c964ee6ffe6glb4jctqZ05Xj", "part": {"id": "prt_zzzzzzzzzzzz"}})
    out = cap.scrub(raw, reps)
    assert str(tmp) not in out and "/kit/dir" not in out and "/venv/bin/python" not in out
    assert "<python> -m demo_smoke dryrun <tmp>/s.json --out <tmp>/out" in out
    assert "<kit>/demo-output/x" in out and "<tmp>\\\\win\\\\path" in out
    assert out.count("ses_0001") == 3 and "msg_0001" in out and "prt_0001" in out and "prt_0002" in out
    assert "call_0_0" in out                                    # the fake's short ids are left alone
    assert not re.search(r"(ses|msg|prt)_[A-Za-z0-9]{10,}", out)
    first = json.loads(out.splitlines()[0])
    assert first["sessionID"] == first["part"]["sessionID"] == "ses_0001"
    # idempotent on an already scrubbed stream, and a no-op without replacements
    assert cap.scrub(out, reps) == out
    assert cap.scrub("nothing here", None) == "nothing here"


def test_write_sample_normalises_newlines(tmp_path):
    c = cap.Capture(stdout='{"type":"text","part":{"text":"a"}}\r\n{"type":"text","part":{"text":"b"}}',
                    stderr="", returncode=0, seconds=1.0, commands=[], replacements=[])
    dest = cap.write_sample(tmp_path / "s.jsonl", c, raw=tmp_path / "r.jsonl")
    data = dest.read_bytes()
    assert data.endswith(b"\n") and b"\r" not in data and data.count(b"\n") == 2
    assert (tmp_path / "r.jsonl").read_bytes().decode("utf-8") == c.stdout
    assert oe.parse_file(dest)["assistant_text"] == ["a", "b"]


def test_golden_commands_match_the_e2e_script(tmp_path):
    """The sample was captured with the same command list the real-binary e2e test asserts on."""
    from tests.opencode_fake_llm import kit_command

    scenario, out = tmp_path / "scenario" / "pass.json", tmp_path / "out"
    cmds = cap.golden_commands(scenario, out, python="/py")
    assert [oe.kit_command_of(c) for c in cmds] == GOLDEN
    assert cmds[1] == kit_command("dryrun", str(scenario), "--out", str(out), "--headless", python="/py")
    assert cmds[5] == kit_command("record", str(scenario), "--out", str(out), "--capture", "screencast",
                                  "--headless", python="/py")
    assert all(c.startswith("/py -m demo_smoke ") for c in cmds)


def test_missing_prerequisite_message(monkeypatch):
    monkeypatch.setattr(cap, "OPENCODE_BIN", "/definitely/not/here/opencode")
    assert "OpenCode binary not found" in cap.missing_prerequisite()
    assert cap.main(["--dest", "/dev/null"]) == 3
