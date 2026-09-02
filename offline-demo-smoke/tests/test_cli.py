"""CLI tests.  Browser/ffmpeg-heavy modules (drive, edit, verify) are replaced by
fakes injected into ``sys.modules`` so these tests exercise orchestration, exit
codes, logs and the report without a browser."""

import json
import sys
import time
import types
from pathlib import Path

import pytest

from demo_smoke import cli, markers, pacing
from demo_smoke.env import Paths

TONE = ["--tts", "tone"]


def run(*argv):
    return cli.main([str(a) for a in argv])


def install_fakes(monkeypatch, *, dry_verdict="PASS", record_fail=False, verify_pass=True,
                  raise_in=None, calls=None):
    calls = calls if calls is not None else []

    def dryrun(scenario, out, headless=False):
        calls.append(("dryrun", headless))
        if raise_in == "dryrun":
            raise RuntimeError("Chrome exited with code 1")
        p = Paths(out)
        (p.logs / "smoke-results.md").write_text("# results\n")
        steps = [{"id": s["id"], "title": s["title"], "status": "PASS", "expected": "e", "observed": "o",
                  "screenshot": str(p.logs / f"step-0{i + 1}-{s['id']}.png"), "seconds": 1.0, "error": None}
                 for i, s in enumerate(scenario["steps"])]
        if dry_verdict == "FAIL":
            steps[-1].update(status="FAIL", error="expectation not met: .answer missing")
        res = {"verdict": dry_verdict, "steps": steps, "console_errors": ["ReferenceError: x"],
               "failed_requests": [], "attempts": 1}
        (p.logs / "dryrun.json").write_text(json.dumps(res))
        return res

    def record(scenario, out, capture, headless, durations):
        calls.append(("record", capture, headless, dict(durations)))
        if raise_in == "record":
            raise RuntimeError("Page.startScreencast failed")
        p = Paths(out)
        plan = pacing.plan([s["id"] for s in scenario["steps"]], durations)
        m = markers.new(time.time())
        for s in scenario["steps"]:
            t0 = plan["steps"][s["id"]]
            markers.add_step(m, s["id"], t0, t0 + 2.0, "FAIL" if record_fail else "PASS", [[t0 + 0.5, t0 + 1.5]])
        m["outro_t"], m["end_t"] = plan["outro_t"], plan["end_t"]
        (p.raw / "capture.mp4").write_bytes(b"\0")
        markers.save(m, out)
        return m

    def build(out, scenario):
        calls.append(("edit",))
        if raise_in == "edit":
            raise RuntimeError("ffmpeg exited 1: Invalid data")
        p = Paths(out)
        final = p.final / f"{scenario['slug']}.mp4"
        final.write_bytes(b"\0")
        (p.logs / "edit.json").write_text("{}")
        return final

    def check(out, scenario):
        calls.append(("verify",))
        res = {"pass": verify_pass, "duration": 30.0, "thumbnails": [],
               "checks": [{"name": "duration", "pass": True, "detail": "ok"},
                          {"name": "audible", "pass": verify_pass, "detail": "mean -12 dB" if verify_pass else "silent"}]}
        (Paths(out).logs / "verify.json").write_text(json.dumps(res))
        return res

    for name, attrs in {"drive": {"dryrun": dryrun, "record": record}, "edit": {"build": build},
                        "verify": {"check": check}}.items():
        mod = types.ModuleType(f"demo_smoke.{name}")
        for k, v in attrs.items():
            setattr(mod, k, v)
        monkeypatch.setitem(sys.modules, f"demo_smoke.{name}", mod)
    return calls


def test_help_and_bad_input(capsys):
    assert run("--help") == 0
    assert "doctor" in capsys.readouterr().out
    assert run("bogus") == 4
    assert run() == 4
    assert run("record", "s.json", "--capture", "nope") == 4
    assert "error:" in capsys.readouterr().err


def test_doctor_writes_log(out_dir, capsys):
    assert run("doctor", "--out", out_dir) == 0
    out = capsys.readouterr().out
    assert out.startswith("doctor: ok ffmpeg=ok chrome=ok")
    rep = json.loads((out_dir / "logs" / "doctor.json").read_text())
    assert rep["ffmpeg"] and rep["chrome"]


def test_doctor_with_missing_tools_and_llm(out_dir, capsys, monkeypatch, tmp_path, unreachable_url):
    monkeypatch.setenv("DEMO_SMOKE_CHROME", str(tmp_path / "nochrome"))
    assert run("doctor", "--out", out_dir, "--base-url", unreachable_url) == 3
    out = capsys.readouterr().out
    assert "chrome=MISSING" in out and "llm=UNREACHABLE" in out and "hint:" in out


def test_check_model(out_dir, fake_llm, capsys, unreachable_url):
    fake_llm.queue.append({"tool_calls": [{"name": "get_step_status", "arguments": {"step_id": "open"}}]})
    assert run("check-model", "--base-url", fake_llm.base_url, "--model", "m", "--out", out_dir) == 0
    assert capsys.readouterr().out.startswith("check-model: PASS")
    fake_llm.queue.append({"content": "no tools for you"})
    assert run("check-model", "--base-url", fake_llm.base_url, "--model", "m", "--out", out_dir) == 2
    assert capsys.readouterr().out.startswith("check-model: FAIL")
    assert json.loads((out_dir / "logs" / "check-model.json").read_text())["pass"] is False
    assert run("check-model", "--base-url", unreachable_url, "--model", "m", "--out", out_dir) == 3
    assert "not reachable" in capsys.readouterr().err
    assert run("check-model", "--base-url", fake_llm.base_url) == 4   # --model required


def test_narrate_template_validate_synth_voice_check(out_dir, example_scenario_path, capsys):
    assert run("narrate-template", example_scenario_path, "--out", out_dir) == 0
    assert capsys.readouterr().out.startswith("narrate-template: ok 4 steps")
    narr = json.loads((out_dir / "audio" / "narration.json").read_text())
    assert [s["id"] for s in narr["steps"]] == ["open", "upload", "ask", "citation"]
    assert (out_dir / "logs" / "narrate-template.json").is_file()
    assert (out_dir / "logs" / "scenario.json").is_file()

    assert run("narrate-validate", "--out", out_dir) == 0            # scenario from logs/scenario.json
    assert run("narrate-validate", example_scenario_path, "--out", out_dir) == 0
    assert run("narrate-validate", "--out", out_dir, "--max-seconds", "5") == 4
    assert "INVALID" in capsys.readouterr().out
    narr["steps"][0]["id"] = "zzz"
    (out_dir / "audio" / "narration.json").write_text(json.dumps(narr))
    assert run("narrate-validate", "--out", out_dir) == 4
    assert "step ids must be exactly" in capsys.readouterr().out
    assert run("narrate-template", example_scenario_path, "--out", out_dir) == 0

    assert run("synth", "--out", out_dir, *TONE) == 0
    assert "synth: ok backend=tone 6 segments" in capsys.readouterr().out
    d = json.loads((out_dir / "audio" / "durations.json").read_text())
    assert list(d) == ["intro", "open", "upload", "ask", "citation", "outro"]
    assert all((out_dir / "audio" / f"seg-{k}.wav").is_file() for k in d)

    assert run("voice-check", "--out", out_dir, *TONE) == 0
    out = capsys.readouterr().out
    assert out.startswith("voice-check: ok backend=tone") and "silent=False" in out
    assert (out_dir / "audio" / "voice_check.wav").is_file()
    assert run("voice-check", "--out", out_dir, *TONE, "--ref", out_dir / "missing.wav") == 4


def test_narrate_llm(out_dir, simple_scenario_path, fake_llm, capsys):
    fake_llm.queue.append({"content": json.dumps({"intro": "Model intro.", "outro": "Model outro.",
                                                  "steps": [{"id": i, "text": f"Model {i}."} for i in ("open", "upload", "ask")]})})
    assert run("narrate-llm", simple_scenario_path, "--out", out_dir, "--base-url", fake_llm.base_url, "--model", "m") == 0
    assert "source=llm" in capsys.readouterr().out
    assert json.loads((out_dir / "audio" / "narration.json").read_text())["intro"] == "Model intro."
    fake_llm.queue.append({"content": "nope"})
    fake_llm.queue.append({"content": "still nope"})
    assert run("narrate-llm", simple_scenario_path, "--out", out_dir, "--base-url", fake_llm.base_url, "--model", "m") == 0
    assert "source=template" in capsys.readouterr().out
    log = json.loads((out_dir / "logs" / "narrate-llm.json").read_text())
    assert log["source"] == "template" and "fell back" in log["note"]


def test_missing_prerequisites_are_exit_3(out_dir, simple_scenario_path, capsys, monkeypatch):
    assert run("synth", "--out", out_dir, *TONE) == 3
    assert "narrate-template" in capsys.readouterr().err
    install_fakes(monkeypatch)
    assert run("record", simple_scenario_path, "--out", out_dir) == 3
    assert "run synth first" in capsys.readouterr().err
    assert run("edit", "--out", out_dir / "empty") == 3
    assert "no scenario" in capsys.readouterr().err
    assert run("synth", "--out", out_dir, "--tts", "turbo") == 3   # narration exists? no -> still 3
    assert run("narrate-validate", "--out", out_dir / "empty2") == 3


def test_scenario_errors_are_exit_4(out_dir, tmp_path, capsys, example_scenario_path):
    assert run("narrate-template", tmp_path / "nope.json", "--out", out_dir) == 4
    assert "not found" in capsys.readouterr().err
    bad = tmp_path / "bad.json"
    bad.write_text('{"name": "x", "app_url": "http://x", "steps": [{"id": "A"}]}')
    assert run("narrate-template", bad, "--out", out_dir) == 4
    err = capsys.readouterr().err
    assert err.startswith("error:") and "steps[0].id" in err and "Traceback" not in err
    # dryrun checks upload fixtures exist
    assert run("dryrun", example_scenario_path, "--out", out_dir, "--headless") == 4
    assert "osha-1910.pdf" in capsys.readouterr().err


def test_dryrun_record_edit_verify_with_fakes(out_dir, simple_scenario_path, capsys, monkeypatch):
    calls = install_fakes(monkeypatch, dry_verdict="FAIL", verify_pass=False)
    assert run("dryrun", simple_scenario_path, "--out", out_dir, "--headless") == 2
    out = capsys.readouterr().out
    assert out.startswith("dryrun: FAIL") and "ask=FAIL" in out
    assert calls[0] == ("dryrun", True)
    assert json.loads((out_dir / "logs" / "dryrun.json").read_text())["verdict"] == "FAIL"

    run("narrate-template", simple_scenario_path, "--out", out_dir)
    run("synth", "--out", out_dir, *TONE)
    capsys.readouterr()
    assert run("record", simple_scenario_path, "--out", out_dir, "--capture", "screen") == 0
    assert capsys.readouterr().out.startswith("record: ok capture=screen open=PASS")
    assert calls[-1][0:3] == ("record", "screen", False)
    assert calls[-1][3]["intro"] > 0
    assert markers.load(out_dir)["steps"][0]["id"] == "open"

    assert run("edit", "--out", out_dir) == 0            # scenario from logs/scenario.json
    assert "final" in capsys.readouterr().out
    assert (out_dir / "final" / "tiny-app.mp4").is_file()
    assert run("verify", "--out", out_dir) == 2
    assert "failed: audible" in capsys.readouterr().out


def test_run_pass(out_dir, simple_scenario_path, capsys, monkeypatch):
    calls = install_fakes(monkeypatch)
    code = run("run", simple_scenario_path, "--out", out_dir, *TONE, "--capture", "screencast",
               "--narration", "template", "--headless")
    out = capsys.readouterr().out
    assert code == 0, out
    stages = [line.split("]")[0][1:] for line in out.splitlines() if line.startswith("[")]
    assert stages == ["doctor", "dryrun", "narrate", "synth", "record", "edit", "verify"]
    assert out.splitlines()[-1].startswith("run: PASS")
    assert [c[0] for c in calls] == ["dryrun", "record", "edit", "verify"]
    res = json.loads((out_dir / "result.json").read_text())
    assert res["verdict"] == "PASS" and res["narration_source"] == "template"
    assert res["final_video"] == "final/tiny-app.mp4"
    md = (out_dir / "report.md").read_text()
    assert "**Verdict: PASS**" in md and "| audible | PASS |" in md
    for f in ("logs/run.json", "logs/doctor.json", "logs/dryrun.json", "logs/record.json",
              "audio/narration.json", "audio/durations.json", "logs/markers.json"):
        assert (out_dir / f).is_file(), f
    assert json.loads((out_dir / "logs" / "run.json").read_text())["timings"]["total"] >= 0


def test_run_dryrun_fail_stops_early(out_dir, simple_scenario_path, capsys, monkeypatch):
    calls = install_fakes(monkeypatch, dry_verdict="FAIL")
    assert run("run", simple_scenario_path, "--out", out_dir, *TONE, "--headless") == 2
    assert [c[0] for c in calls] == ["dryrun"]
    res = json.loads((out_dir / "result.json").read_text())
    assert res["verdict"] == "FAIL"
    md = (out_dir / "report.md").read_text()
    assert "expectation not met" in md and "ReferenceError: x" in md
    assert "run: FAIL" in capsys.readouterr().out


def test_run_verify_fail(out_dir, simple_scenario_path, capsys, monkeypatch):
    install_fakes(monkeypatch, verify_pass=False)
    assert run("run", simple_scenario_path, "--out", out_dir, *TONE, "--headless") == 2
    res = json.loads((out_dir / "result.json").read_text())
    assert res["verdict"] == "FAIL" and "audible" in res["error"]


def test_run_record_step_failure(out_dir, simple_scenario_path, monkeypatch):
    calls = install_fakes(monkeypatch, record_fail=True)
    assert run("run", simple_scenario_path, "--out", out_dir, *TONE, "--headless") == 2
    assert [c[0] for c in calls] == ["dryrun", "record"]
    assert "during recording" in json.loads((out_dir / "result.json").read_text())["error"]


@pytest.mark.parametrize("stage", ["dryrun", "record", "edit"])
def test_run_pipeline_error_is_exit_3_with_report(out_dir, simple_scenario_path, capsys, monkeypatch, stage):
    install_fakes(monkeypatch, raise_in=stage)
    assert run("run", simple_scenario_path, "--out", out_dir, *TONE, "--headless") == 3
    captured = capsys.readouterr()
    assert f"{stage} failed:" in captured.err and "Traceback" not in captured.err
    md = (out_dir / "report.md").read_text()
    assert "**Verdict: ERROR**" in md and f"{stage} failed:" in md
    assert json.loads((out_dir / "result.json").read_text())["verdict"] == "ERROR"


def test_run_missing_tool_is_exit_3(out_dir, simple_scenario_path, monkeypatch, tmp_path, capsys):
    calls = install_fakes(monkeypatch)
    monkeypatch.setenv("DEMO_SMOKE_CHROME", str(tmp_path / "nochrome"))
    assert run("run", simple_scenario_path, "--out", out_dir, *TONE) == 3
    assert calls == []
    assert "chrome missing" in capsys.readouterr().err
    assert "chrome missing" in (out_dir / "report.md").read_text()


def test_run_with_llm_narration(out_dir, simple_scenario_path, monkeypatch, fake_llm, capsys):
    install_fakes(monkeypatch)
    fake_llm.queue.append({"tool_calls": [{"name": "get_step_status", "arguments": {"step_id": "open"}}]})
    fake_llm.queue.append({"content": json.dumps({"intro": "LLM intro.", "outro": "LLM outro.",
                                                  "steps": [{"id": i, "text": f"LLM {i}."} for i in ("open", "upload", "ask")]})})
    assert run("run", simple_scenario_path, "--out", out_dir, *TONE, "--headless", "--narration", "llm",
               "--base-url", fake_llm.base_url, "--model", "m") == 0
    out = capsys.readouterr().out
    assert "[narrate] source=llm" in out
    res = json.loads((out_dir / "result.json").read_text())
    assert res["narration_source"] == "llm" and res["env"]["llm"]["tool_call"]["pass"] is True
    assert run("run", simple_scenario_path, "--out", out_dir, *TONE, "--headless", "--narration", "llm") == 3
    assert "needs --base-url" in capsys.readouterr().err


def test_run_bad_scenario_is_exit_4(out_dir, tmp_path, monkeypatch, capsys):
    install_fakes(monkeypatch)
    assert run("run", tmp_path / "nope.json", "--out", out_dir) == 4
    assert "not found" in capsys.readouterr().err


def test_main_module_entry():
    import runpy
    with pytest.raises(SystemExit) as ei:
        runpy.run_module("demo_smoke", run_name="__main__", alter_sys=True)
    assert ei.value.code == 4  # no argv -> usage error


def test_json_safe_scenario_roundtrip(out_dir, simple_scenario_path):
    from demo_smoke import scenario
    paths = Paths(out_dir)
    scen = cli._load_scenario(str(simple_scenario_path), paths)
    saved = json.loads((paths.logs / "scenario.json").read_text())
    assert saved["_dir"] == str(scen["_dir"])
    again = cli._scenario_for(types.SimpleNamespace(scenario=None), paths)
    assert isinstance(again["_dir"], Path) and again["slug"] == scen["slug"]
    assert scenario.validate(again) == []
