"""End-to-end: ``bench`` runs the fixture scenario under the ``template`` driver (a real
``python -m demo_smoke run`` subprocess) and under ``opencode:fake/scripted@<fake-url>`` (the REAL
OpenCode binary driven by the scripted fake model from ``tests/opencode_fake_llm.py``), then writes
``report.md`` + ``bench.json``.  Headless Chromium, ``tone`` TTS.  Skipped only when the OpenCode
binary (``OPENCODE_BIN``) or a Chrome is missing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from demo_smoke import bench, bench_report, chrome
from tests.fixtures.serve import serve_dir
from tests.opencode_fake_llm import FakeOpenCodeLLM, kit_command

KIT = Path(__file__).resolve().parents[1]
APP_DIR = KIT / "tests" / "fixtures" / "app"
SCEN_DIR = KIT / "tests" / "fixtures" / "scenarios"
OPENCODE_BIN = os.environ.get("OPENCODE_BIN", bench.DEFAULT_OPENCODE)
TIMEOUT_S = 600


def _need_opencode_and_chrome() -> None:
    if not Path(OPENCODE_BIN).is_file():
        pytest.skip(f"OpenCode binary not found: {OPENCODE_BIN} (set OPENCODE_BIN)")
    if not chrome.find_chrome():
        pytest.skip("no Chrome binary available (set DEMO_SMOKE_CHROME)")


def _write_scenario(name: str, base_url: str, dest: Path) -> Path:
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


def test_bench_template_and_opencode_drivers(tmp_path, monkeypatch):
    _need_opencode_and_chrome()
    # The bench leaves HOME alone (a hosted provider needs the user's OpenCode auth); the test
    # points the *process* HOME at a scratch dir so nothing lands in the real ~/.local/share/opencode.
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME",
                "OPENCODE_CONFIG", "OPENCODE_CONFIG_DIR", "OPENCODE_PERMISSION", "OPENCODE_CONFIG_CONTENT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OPENCODE_BIN", OPENCODE_BIN)
    out = tmp_path / "bench"
    driver = bench.parse_driver("opencode:fake/scripted@http://127.0.0.1:1/v1")   # url patched below
    agent_out = bench.run_dir(out, driver, 1)

    with serve_dir(APP_DIR) as base:
        scenario = _write_scenario("fixture-pass.json", base, tmp_path / "scenario" / "pass.json")
        commands = [
            kit_command("doctor", "--out", str(agent_out)),
            kit_command("dryrun", str(scenario), "--out", str(agent_out), "--headless"),
            kit_command("narrate-template", str(scenario), "--out", str(agent_out)),
            kit_command("narrate-validate", str(scenario), "--out", str(agent_out)),
            kit_command("synth", "--out", str(agent_out), "--tts", "tone"),
            kit_command("record", str(scenario), "--out", str(agent_out), "--capture", "screencast", "--headless"),
            kit_command("edit", "--out", str(agent_out)),
            kit_command("verify", "--out", str(agent_out)),
        ]
        with FakeOpenCodeLLM(commands, log_path=tmp_path / "fake-llm.jsonl") as fake:
            argv = [str(scenario), "--out", str(out), "--driver", "template",
                    "--driver", f"opencode:fake/scripted@{fake.base_url}",
                    "--tts", "tone", "--headless", "--timeout-s", str(TIMEOUT_S),
                    "--baseline", str(KIT / "bench" / "baseline.example.json")]
            code = bench.main(argv)
            fake_log = fake.log_path.read_text(encoding="utf-8")[-3000:] if fake.log_path.is_file() else ""
            issued = list(fake.tool_calls_issued)
            fake_errors = list(fake.errors)

    report_path = out / "report.md"
    json_path = out / "bench.json"
    detail = (f"exit={code}\n--- report.md ---\n{report_path.read_text(encoding='utf-8') if report_path.is_file() else '(missing)'}"
              f"\n--- fake log (tail) ---\n{fake_log}")
    for p in (agent_out / "logs" / "bench-stdout.txt", agent_out / "logs" / "bench-stderr.txt"):
        if p.is_file():
            detail += f"\n--- {p.name} (tail) ---\n{p.read_text(encoding='utf-8')[-4000:]}"
    assert report_path.is_file() and json_path.is_file(), detail
    assert fake_errors == [], detail
    assert issued == commands, detail
    assert code == 0, detail

    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert bench_report.validate(data) == [], (bench_report.validate(data), detail)
    assert (out / "logs" / "bench.json").is_file()
    runs = {(r["slug"], r["run"]): r for r in data["runs"]}
    assert set(runs) == {("template", 1), ("opencode-fake-scripted", 1)}, detail
    tpl, oc = runs[("template", 1)], runs[("opencode-fake-scripted", 1)]

    # both rows PASS, with real artifacts behind them
    for r in (tpl, oc):
        assert r["verdict"] == "PASS", (r["verdict"], r["error"], r["failing_stage"], detail)
        assert r["exit_code"] == 0 and r["error"] is None
        run_out = Path(r["out"])
        assert run_out.parent.parent == out / "runs"
        assert (run_out / "final" / "fixture-pass.mp4").stat().st_size > 10_000
        assert (run_out / "bench.json").is_file()
        assert r["video"]["path"] == "final/fixture-pass.mp4" and r["video"]["verify_pass"] is True
        assert all(c["pass"] for c in r["video"]["checks"]) and r["video"]["duration"] > 5
        assert r["narration"]["source"] == "template"
        assert r["narration"]["total_words"] > 20 and r["narration"]["segments"] == 5
        assert set(r["narration"]["words_per_segment"]) == {"intro", "open", "upload", "ask", "outro"}
        assert 0.0 <= r["narration"]["references_on_screen"] <= 1.0
        assert r["narration"]["validation_errors"] == 0 and r["narration"]["retries"] == 0
        assert r["audio"]["total_seconds"] > 5 and set(r["audio"]["segments"]) == {"intro", "open", "upload", "ask", "outro"}
        assert r["wall_s"] > 5 and r["started"] < r["finished"]
        assert r["env"]["chrome"] and r["env"]["ffmpeg"]
        assert r["stages"]["record"] and r["stages"]["dryrun"] and r["stages"]["verify"] is not None
    # the two drivers spoke the same template text, so the on-screen metric must agree
    assert tpl["narration"]["references_on_screen"] == oc["narration"]["references_on_screen"]
    assert tpl["narration"]["total_words"] == oc["narration"]["total_words"]

    # template driver: the kit's own run report
    assert tpl["kind"] == "template" and tpl["opencode"] is None and tpl["llm"] is None
    assert tpl["report"] == "report.md" and (Path(tpl["out"]) / "result.json").is_file()
    assert tpl["stages"]["doctor"] is not None and tpl["stages"]["narrate"] is not None

    # opencode driver: the real binary, >= 8 tool calls, no permission prompts, no denials
    assert oc["kind"] == "opencode" and oc["model"] == "fake/scripted" and oc["llm"] is None
    agent = oc["opencode"]
    assert agent["tool_calls"] >= 8, (agent, detail)
    assert agent["kit_tool_calls"] == 8 and agent["kit_commands"] == [
        "doctor", "dryrun", "narrate-template", "narrate-validate", "synth", "record", "edit", "verify"]
    assert agent["commands"] == commands
    assert agent["permission_prompts"] == 0 and agent["denied"] == 0
    assert agent["failed_tool_calls"] == [] and agent["errors"] == []
    assert agent["assistant_messages"] >= 1 and agent["steps"] >= 8
    assert agent["steps_limit"] == 60 and agent["step_limit_reached"] is False
    assert agent["tokens_total"] and agent["tokens_in"] and agent["tokens_out"]     # the fake reports usage
    assert agent["cost"] is None                # OpenCode has no price for the override model: not "$0.0000"
    assert agent["narration_written_by_agent"] is False and agent["used_narrate_template"] is True
    assert agent["final_status"] == "completed" and agent["session_id"]
    assert agent["parser"] == "opencode_events"
    assert (agent_out / "logs" / "opencode-events.json").is_file()
    assert (agent_out / "logs" / "bench-stdout.txt").read_text(encoding="utf-8").count('"type":"tool_use"') >= 8
    assert oc["argv"][:5] == [OPENCODE_BIN, "run", "--agent", "demo-smoke", "--auto"]
    assert "--command" in oc["argv"] and oc["argv"][-1].endswith(" tts:tone headless")   # --tts reaches the agent
    # OpenCode wrote under the (scratch) HOME the process had - the bench did not change it
    assert (home / ".local" / "share" / "opencode").is_dir()

    # the report: one table with both rows and the manual baseline, differences, links
    md = report_path.read_text(encoding="utf-8")
    assert "# Bench: Chat with Manuals (fixture, pass)" in md
    header = "| driver | model | verdict | total min | narration | tool calls | words | on-screen refs | validation retries | video s | tokens / cost | notes |"
    assert header in md
    results = md.split("## Results", 1)[1].split("## What differed", 1)[0]
    rows = [ln for ln in results.splitlines()
            if ln.startswith(("| template |", "| opencode:fake/scripted |", "| manual |"))]
    assert len(rows) == 4, md                         # template, opencode, 2 manual entries
    assert rows[0].split("|")[3].strip() == "PASS" and rows[1].split("|")[3].strip() == "PASS"
    assert "| manual | codex (cloud) | PASS | 95 | cloud model |" in md
    assert "## What differed" in md and "ran 8 kit commands per run, at or above the playbook minimum of 7" in md
    assert "tool calls per run, including file reads" in md and "in total" not in md
    assert "$0.0000" not in md
    assert "Manual baseline codex (cloud) took 95 min (PASS) per its own notes" in md
    assert "| driver slug | run | verdict | min |" in md
    assert "[report.md](runs/template/r1/report.md)" in md
    assert "[video](runs/template/r1/final/fixture-pass.mp4)" in md
    assert "[video](runs/opencode-fake-scripted/r1/final/fixture-pass.mp4)" in md
    assert "[events](runs/opencode-fake-scripted/r1/logs/opencode-events.json)" in md
    assert "## Appendix: per-run rows" in md
    assert "<" not in md.replace("<=", "")           # plain markdown, no HTML
    assert data["rows"][0]["slug"] == "template" and data["rows"][1]["slug"] == "opencode-fake-scripted"
    assert data["baseline"][0]["model"] == "codex (cloud)"
    assert data["repeat"] == 1 and data["args"]["tts"] == "tone" and data["args"]["headless"] is True
