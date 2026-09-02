"""``demo_smoke.bench`` metrics on synthetic run directories, driver parsing, and the report."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from demo_smoke import bench, bench_report

SCENARIO = {
    "name": "Chat with Manuals (fixture, pass)", "slug": "fixture-pass", "app_url": "http://127.0.0.1:8765",
    "viewport": {"width": 1280, "height": 720}, "login": {"type": "none"}, "max_length_seconds": 60,
    "intro": "This is the offline Chat with Manuals demo.", "outro": "That is the whole flow.",
    "steps": [
        {"id": "open", "title": "Open the app", "actions": [{"goto": "/"}],
         "expect": [{"text": "Chat with Manuals"}, {"url_contains": "127.0.0.1"}]},
        {"id": "upload", "title": "Upload manuals",
         "actions": [{"upload": {"selector": "input[type=file]", "files": ["a.pdf"]}}],
         "expect": [{"selector": ".doc-chip", "count_min": 2}, {"selector": ".doc-chip", "contains": "manual-a.pdf"}]},
        {"id": "ask", "title": "Ask a question", "actions": [{"click": "button"}],
         "expect": [{"selector": ".answer", "contains": "inspect"}, {"text": "[1] manual-a.pdf p.3"},
                    {"not_text": "could not find"}]},
    ],
}
NARRATION = {
    "intro": "This is the offline Chat with Manuals demo running against a local build.",
    "outro": "And we are done here.",
    "steps": [
        {"id": "open", "text": "First we open the app and land on the home screen."},
        {"id": "upload", "text": "Next we upload two equipment manuals."},
        {"id": "ask", "text": "Then we type something and wait for the reply."},
    ],
}
DURATIONS = {"intro": 5.2, "open": 3.6, "upload": 2.4, "ask": 2.8, "outro": 2.0}
VERIFY = {"pass": True, "duration": 24.5, "thumbnails": [],
          "checks": [{"name": "duration", "pass": True, "detail": "ok"},
                     {"name": "av_length_match", "pass": True, "detail": "ok"},
                     {"name": "narration_audible", "pass": True, "detail": "ok"}]}
DOCTOR = {"os": "Linux", "python": "3.11", "ffmpeg": "/usr/bin/ffmpeg", "chrome": "/usr/bin/chrome",
          "torch_device": "none", "chatterbox": False, "hints": []}


def _write(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")


def make_run_dir(root: Path, *, verdict: str = "PASS", narration: dict | None = NARRATION, source: str = "template",
                 with_run_json: bool = True, stage: str = "report", video: bool = True,
                 timings: dict | None = None, error: str | None = None) -> Path:
    """A fake kit output directory the way ``run`` (or the agent's step commands) leave it."""
    root.mkdir(parents=True, exist_ok=True)
    dry_steps = [{"id": s["id"], "title": s["title"], "status": "PASS", "expected": "e", "observed": "o",
                  "screenshot": None, "seconds": 1.0, "error": None} for s in SCENARIO["steps"]]
    if verdict == "FAIL" and stage == "dryrun":
        dry_steps[2].update(status="FAIL", error="expectation not met")
    dry = {"verdict": "FAIL" if (verdict == "FAIL" and stage == "dryrun") else "PASS", "steps": dry_steps,
           "console_errors": [], "failed_requests": [], "attempts": 1}
    _write(root / "logs" / "dryrun.json", dry)
    _write(root / "logs" / "doctor.json", DOCTOR)
    if narration is not None:
        _write(root / "audio" / "narration.json", narration)
        _write(root / "audio" / "durations.json", DURATIONS)
    if video:
        (root / "final").mkdir(exist_ok=True)
        (root / "final" / "fixture-pass.mp4").write_bytes(b"\0" * 32)
        _write(root / "logs" / "verify.json", VERIFY)
    if with_run_json:
        t = timings or {"doctor": 0.4, "dryrun": 6.1, "narrate": 0.0, "synth": 1.2, "record": 20.3, "edit": 4.0,
                        "verify": 2.2, "total": 34.9}
        _write(root / "logs" / "run.json", {"verdict": verdict, "error": error, "stage": stage,
                                             "narration_source": source, "timings": t})
        _write(root / "result.json", {"verdict": verdict, "error": error, "narration_source": source,
                                      "dryrun": dry, "verify": VERIFY if video else None,
                                      "final_video": "final/fixture-pass.mp4" if video else None})
        (root / "report.md").write_text("# Demo smoke report\n", encoding="utf-8")
    return root


def _events(commands: list[str], tokens: int = 120, cost: float = 0.0, wrote_narration: bool = False) -> dict:
    """What ``bench.opencode_summary`` returns for a scripted run (shape of ``opencode_events.summary``)."""
    kit = [c.split("-m demo_smoke ")[1].split()[0] for c in commands]
    stages = {s: None for s in bench.STAGES}
    for k in kit:
        st = "narrate" if k.startswith("narrate") else k
        if st in stages:
            stages[st] = (stages[st] or 0) + 1.5
    return {"parser": "opencode_events", "tool_calls": len(commands) + (1 if wrote_narration else 0),
            "commands": commands, "kit_commands": kit, "failed_tool_calls": [], "assistant_messages": 1,
            "permission_prompts": 0, "denied": 0, "steps": len(commands) + 1, "step_limit_reached": False,
            "tokens_in": tokens - 20, "tokens_out": 20, "tokens_total": tokens, "cost": cost, "errors": [],
            "wall_s": 40.0, "stages": stages, "narration_written_by_agent": wrote_narration,
            "used_narrate_template": "narrate-template" in kit, "used_narrate_llm": False,
            "final_status": "completed", "session_id": "ses_x"}


PLAYBOOK = [f"python -m demo_smoke {c}" for c in
            ("doctor --out o", "dryrun s --out o --headless", "narrate-template s --out o", "narrate-validate s --out o",
             "synth --out o --tts tone", "record s --out o --capture screencast --headless", "edit --out o",
             "verify --out o")]


# ----------------------------------------------------------------------------- driver parsing


@pytest.mark.parametrize("spec, kind, model, base, slug", [
    ("template", "template", None, None, "template"),
    ("llm:http://127.0.0.1:1234/v1|qwen3:14b", "llm", "qwen3:14b", "http://127.0.0.1:1234/v1", "llm-qwen3-14b"),
    ("llm:http://localhost:11434/v1|a|b", "llm", "b", "http://localhost:11434/v1|a", "llm-b"),
    ("opencode:lmstudio/local", "opencode", "lmstudio/local", None, "opencode-lmstudio-local"),
    ("opencode:anthropic/claude-sonnet-4-5", "opencode", "anthropic/claude-sonnet-4-5", None,
     "opencode-anthropic-claude-sonnet-4-5"),
    ("opencode:fake/scripted@http://127.0.0.1:5/v1", "opencode", "fake/scripted", "http://127.0.0.1:5/v1",
     "opencode-fake-scripted"),
    ("OpenCode:ollama/qwen3-coder:30b@http://10.0.0.2:11434/v1", "opencode", "ollama/qwen3-coder:30b",
     "http://10.0.0.2:11434/v1", "opencode-ollama-qwen3-coder-30b"),
])
def test_parse_driver(spec, kind, model, base, slug):
    d = bench.parse_driver(spec)
    assert (d.kind, d.model, d.base_url, d.slug) == (kind, model, base, slug)
    assert d.spec == spec.strip()


@pytest.mark.parametrize("spec", ["", "template:x", "llm:qwen3", "llm:|qwen3", "llm:http://x/v1|", "opencode:",
                                  "opencode:nomodel", "opencode:/x", "opencode:x/", "codex:gpt", "llm"])
def test_parse_driver_rejects_bad_specs(spec):
    with pytest.raises(bench.BenchError):
        bench.parse_driver(spec)


def test_parse_drivers_makes_slugs_unique_and_defaults_to_template(tmp_path):
    ds = bench.parse_drivers(["template", "opencode:a/b", "template", "opencode:a/b"])
    assert [d.slug for d in ds] == ["template", "opencode-a-b", "template-2", "opencode-a-b-2"]
    assert [d.slug for d in bench.parse_drivers([])] == ["template"]
    assert bench.run_dir(tmp_path, ds[1], 3) == tmp_path / "runs" / "opencode-a-b" / "r3"
    assert bench.run_dir(tmp_path, "x", 1) == tmp_path / "runs" / "x" / "r1"


def test_opencode_config_override_only_for_at_drivers():
    assert bench.opencode_config_override(bench.parse_driver("opencode:lmstudio/local")) is None
    assert bench.opencode_config_override(bench.parse_driver("template")) is None
    kit_cfg = {"provider": {"lmstudio": {"npm": "@ai-sdk/openai-compatible", "options": {"baseURL": "http://old"}}}}
    known = bench.opencode_config_override(bench.parse_driver("opencode:lmstudio/my-id@http://10.0.0.9:1234/v1"),
                                           kit_cfg)
    assert known["provider"]["lmstudio"] == {"options": {"baseURL": "http://10.0.0.9:1234/v1"},
                                             "models": {"my-id": {"name": "my-id"}}}
    assert "npm" not in known["provider"]["lmstudio"]         # keeps the kit's SDK choice
    assert known["model"] == known["small_model"] == "lmstudio/my-id"
    fresh = bench.opencode_config_override(bench.parse_driver("opencode:fake/scripted@http://127.0.0.1:5/v1"), kit_cfg)
    assert fresh["provider"]["fake"]["npm"] == "@ai-sdk/openai-compatible"
    assert fresh["provider"]["fake"]["options"]["baseURL"] == "http://127.0.0.1:5/v1"
    assert list(fresh["provider"]) == ["fake"]                # no other provider is touched


def test_opencode_env_and_argv(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", "/home/real-user")
    d = bench.parse_driver("opencode:fake/scripted@http://127.0.0.1:5/v1")
    env = bench.opencode_env(d, timeout=90)
    assert env["HOME"] == "/home/real-user"                   # the user's OpenCode auth stays usable
    assert env["OPENCODE_DISABLE_MODELS_FETCH"] == "1"
    assert env["OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS"] == "90000"
    assert json.loads(env["OPENCODE_CONFIG_CONTENT"])["provider"]["fake"]["options"]["baseURL"] == "http://127.0.0.1:5/v1"
    assert str(bench.KIT) in env["PYTHONPATH"].split(os.pathsep)
    # the agent's bare `python -m demo_smoke` must be the interpreter running the bench (venv activated or not)
    assert env["PATH"].split(os.pathsep)[0] == str(Path(sys.executable).parent)
    # a hosted driver without @url: no provider block, but the model is still pinned for the title/summary
    # agents so OpenCode never falls back to the kit's default local ollama model for them
    plain = bench.opencode_env(bench.parse_driver("opencode:anthropic/claude-sonnet-4-5"), timeout=10)
    assert json.loads(plain["OPENCODE_CONFIG_CONTENT"]) == {"model": "anthropic/claude-sonnet-4-5",
                                                            "small_model": "anthropic/claude-sonnet-4-5"}
    assert plain["PYTHONIOENCODING"] == "utf-8"
    argv = bench.opencode_argv("/bin/opencode", d, Path("/s/x.json"), tmp_path / "r1", True, Path("/v/ref.wav"))
    assert argv[:11] == ["/bin/opencode", "run", "--agent", "demo-smoke", "--auto", "--model", "fake/scripted",
                         "--format", "json", "--command", "smoke"]
    # every path is double-quoted: OpenCode splits the message on whitespace unless quoted
    assert argv[11] == f'"/s/x.json" "{tmp_path / "r1"}" headless "/v/ref.wav"'
    assert len(argv) == 12
    spaced = tmp_path / "First Last" / "r 1"
    msg = bench.opencode_argv("/bin/opencode", d, Path("/s/my scenario.json"), spaced, False, None)[-1]
    assert msg == f'"/s/my scenario.json" "{spaced}"'
    assert not msg.endswith(" headless")
    # --tts reaches the agent as a `tts:<backend>` token of the smoke command (before `headless` / the ref)
    msg = bench.opencode_argv("/bin/opencode", d, Path("/s/x.json"), tmp_path, True, Path("/v/r.wav"), tts="tone")[-1]
    assert msg == f'"/s/x.json" "{tmp_path}" tts:tone headless "/v/r.wav"'
    run_argv = bench.kit_run_argv(bench.parse_driver("llm:http://h/v1|m"), Path("/s/x.json"), tmp_path, "tone", True,
                                  None, 30, python="/py")
    assert run_argv == ["/py", "-m", "demo_smoke", "run", "/s/x.json", "--out", str(tmp_path), "--tts", "tone",
                        "--capture", "screencast", "--narration", "llm", "--headless", "--base-url", "http://h/v1",
                        "--model", "m", "--timeout", "30"]
    assert "--narration" in bench.kit_run_argv(bench.parse_driver("template"), Path("s"), tmp_path, "auto", False,
                                               None, 30)


# ----------------------------------------------------------------------------- narration metrics


def test_on_screen_tokens_and_references():
    screen = bench.on_screen_tokens(SCENARIO)
    assert {"chat", "manuals", "open", "app", "upload", "manual", "pdf", "inspect", "ask", "question"} <= screen
    assert "the" not in screen and "127" not in screen and "could" not in screen   # stopwords / url / not_text
    # intro (chat, manuals), open (open, app), upload (upload, manuals) hit; ask + outro do not -> 3/5
    assert bench.references_on_screen(NARRATION, SCENARIO) == 0.6
    assert bench.references_on_screen(None, SCENARIO) is None
    assert bench.references_on_screen({"intro": "The and of", "outro": "", "steps": []}, SCENARIO) == 0.0
    assert bench.tokens("Open the App, then OPEN it quickly!") == {"open", "app", "quickly"}


def test_narration_metrics_counts_words():
    m = bench.narration_metrics(NARRATION, SCENARIO)
    assert m["segments"] == 5
    assert m["words_per_segment"] == {"intro": 13, "open": 11, "upload": 6, "ask": 9, "outro": 5}
    assert m["total_words"] == 44
    assert m["estimated_seconds"] == round(44 / 2.6, 1)
    assert m["references_on_screen"] == 0.6
    empty = bench.narration_metrics(None, SCENARIO)
    assert empty == {"segments": 0, "words_per_segment": {}, "total_words": 0, "estimated_seconds": None,
                     "references_on_screen": None}


def test_llm_metrics_come_from_run_json_not_from_stdout():
    """``run`` writes the structured ``llm`` block; the ``[narrate]`` note is kept verbatim, never parsed."""
    ok = "[doctor] ok\n[narrate] source=llm 3 steps (narration from model)\n[synth] x"
    log = {"narration_source": "llm", "llm": {"attempts": 1, "problems": [], "fallback": False, "fallback_reason": None}}
    assert bench.llm_metrics(ok, log) == {"attempts": 1, "problems": [], "fallback": False, "fallback_reason": None,
                                          "note": "3 steps (narration from model)"}
    repaired = {"narration_source": "llm",
                "llm": {"attempts": 2, "problems": ["attempt 1: steps[0] has 50 words"], "fallback": False,
                        "fallback_reason": None}}
    m = bench.llm_metrics("[narrate] source=llm 3 steps (narration from model after one repair round)", repaired)
    assert m["attempts"] == 2 and m["problems"] == ["attempt 1: steps[0] has 50 words"] and m["fallback"] is False
    # two rejected answers -> 2 attempts, 2 problems; a request failure -> 0 attempts, no problems, its reason kept
    twice = {"narration_source": "template", "llm": {"attempts": 2, "problems": ["attempt 1: x", "attempt 2: y"],
                                                     "fallback": True, "fallback_reason": "rejected twice"}}
    m = bench.llm_metrics("[narrate] source=template 3 steps (fell back ...)", twice)
    assert m["attempts"] == 2 and len(m["problems"]) == 2 and m["fallback"] is True
    failed = {"narration_source": "template", "llm": {"attempts": 0, "problems": [], "fallback": True,
                                                      "fallback_reason": "request failed: boom"}}
    m = bench.llm_metrics("[narrate] source=template 3 steps (fell back to template narration: request failed: boom "
                          "| request failed: boom)", failed)
    assert m["attempts"] == 0 and m["problems"] == [] and m["fallback_reason"] == "request failed: boom"
    # the word "attempt" in a note means nothing on its own
    noisy = bench.llm_metrics("[narrate] source=llm 3 steps (attempt attempt attempt)", {"narration_source": "llm"})
    assert noisy["attempts"] == 0 and noisy["problems"] == [] and noisy["fallback"] is False
    assert bench.llm_metrics("", {"narration_source": "template"})["fallback"] is True
    assert bench.llm_metrics("", None) == {"attempts": 0, "problems": [], "fallback": False, "fallback_reason": None,
                                           "note": None}


# ----------------------------------------------------------------------------- collect


def test_collect_template_run(tmp_path):
    d = bench.parse_driver("template")
    out = make_run_dir(tmp_path / "runs" / "template" / "r1")
    rec = bench.collect(out, d, SCENARIO, n=1, started="2026-09-02T10:00:00+00:00",
                        finished="2026-09-02T10:00:35+00:00", wall_s=35.4, exit_code=0, stdout="run: PASS")
    assert rec["verdict"] == "PASS" and rec["exit_code"] == 0 and rec["error"] is None
    assert rec["failing_stage"] is None and rec["failing_step"] is None
    assert rec["wall_s"] == 35.4
    assert rec["stages"] == {"doctor": 0.4, "dryrun": 6.1, "narrate": 0.0, "synth": 1.2, "record": 20.3,
                             "edit": 4.0, "verify": 2.2}
    assert rec["narration"]["source"] == "template"
    assert rec["narration"]["total_words"] == 44 and rec["narration"]["retries"] == 0
    assert rec["narration"]["validation_errors"] == 0
    assert rec["narration"]["references_on_screen"] == 0.6
    assert rec["audio"]["total_seconds"] == 16.0 and rec["audio"]["segments"]["intro"] == 5.2
    assert rec["video"] == {"duration": 24.5, "verify_pass": True, "path": "final/fixture-pass.mp4",
                            "checks": [{"name": "duration", "pass": True}, {"name": "av_length_match", "pass": True},
                                       {"name": "narration_audible", "pass": True}]}
    assert rec["opencode"] is None and rec["llm"] is None
    assert rec["env"]["os"] == "Linux"
    assert rec["report"] == "report.md" and rec["slug"] == "template" and rec["run"] == 1
    assert bench_report.validate({"scenario": "s", "started": "", "finished": "", "drivers": [], "repeat": 1,
                                  "runs": [rec], "rows": bench_report.aggregate([rec]), "differences": [],
                                  "baseline": []}) == []


def test_collect_wall_from_run_json_when_not_given(tmp_path):
    out = make_run_dir(tmp_path / "r1")
    rec = bench.collect(out, bench.parse_driver("template"), SCENARIO)
    assert rec["wall_s"] == 34.9 and rec["started"] is None


def test_collect_failed_and_errored_runs(tmp_path):
    d = bench.parse_driver("template")
    failed = make_run_dir(tmp_path / "fail", verdict="FAIL", stage="dryrun", video=False)
    rec = bench.collect(failed, d, SCENARIO, exit_code=2)
    assert rec["verdict"] == "FAIL" and rec["failing_stage"] == "dryrun" and rec["failing_step"] == "ask"
    assert rec["video"]["duration"] is None and rec["video"]["path"] is None and rec["video"]["checks"] == []

    errored = make_run_dir(tmp_path / "err", verdict="ERROR", stage="record", video=False,
                           error="record failed: Chrome exited")
    rec = bench.collect(errored, d, SCENARIO, exit_code=3)
    assert rec["verdict"] == "ERROR" and rec["failing_stage"] == "record"
    assert rec["error"] == "record failed: Chrome exited"

    nothing = tmp_path / "nothing"
    nothing.mkdir()
    rec = bench.collect(nothing, d, SCENARIO, exit_code=None, error="timed out after 5 s")
    assert rec["verdict"] == "ERROR" and rec["error"] == "timed out after 5 s"
    assert rec["narration"]["source"] == "template" and rec["narration"]["total_words"] == 0
    assert rec["audio"]["total_seconds"] is None and rec["env"] is None
    assert rec["stages"] == {s: None for s in bench.STAGES}


def test_collect_llm_run_with_fallback(tmp_path):
    d = bench.parse_driver("llm:http://127.0.0.1:1234/v1|qwen3:14b")
    out = make_run_dir(tmp_path / "r1", source="template")
    stdout = ("[narrate] source=template 3 steps (fell back to template narration: attempt 1: no JSON object found "
              "| attempt 2: steps[1] (upload) has 60 words (max 45))")
    (out / "logs" / "bench-stdout.txt").write_text(stdout, encoding="utf-8")
    run_log = json.loads((out / "logs" / "run.json").read_text(encoding="utf-8"))
    run_log["llm"] = {"attempts": 2, "problems": ["attempt 1: no JSON object found",
                                                   "attempt 2: steps[1] (upload) has 60 words (max 45)"],
                      "fallback": True, "fallback_reason": "rejected twice"}
    _write(out / "logs" / "run.json", run_log)
    rec = bench.collect(out, d, SCENARIO, exit_code=0)             # stdout read back from logs/
    assert rec["kind"] == "llm" and rec["model"] == "qwen3:14b"
    assert rec["narration"]["source"] == "template"
    assert rec["llm"] == {"attempts": 2, "problems": run_log["llm"]["problems"], "fallback": True,
                          "fallback_reason": "rejected twice", "note": stdout.split(" ", 2)[2]}
    assert rec["narration"]["retries"] == 1 and rec["narration"]["validation_errors"] == 2
    assert rec["opencode"] is None

    good = make_run_dir(tmp_path / "r2", source="llm")
    run_log = json.loads((good / "logs" / "run.json").read_text(encoding="utf-8"))
    run_log["llm"] = {"attempts": 1, "problems": [], "fallback": False, "fallback_reason": None}
    _write(good / "logs" / "run.json", run_log)
    rec = bench.collect(good, d, SCENARIO, exit_code=0, stdout="[narrate] source=llm 3 steps (narration from model)")
    assert rec["narration"]["source"] == "llm" and rec["llm"]["fallback"] is False and rec["llm"]["attempts"] == 1
    assert rec["narration"]["retries"] == 0 and rec["narration"]["validation_errors"] == 0

    # a request failure that fell back: no attempts answered, no validation problems, the reason kept
    fell = make_run_dir(tmp_path / "r3", source="template")
    run_log = json.loads((fell / "logs" / "run.json").read_text(encoding="utf-8"))
    run_log["llm"] = {"attempts": 0, "problems": [], "fallback": True, "fallback_reason": "request failed: boom"}
    _write(fell / "logs" / "run.json", run_log)
    rec = bench.collect(fell, d, SCENARIO, exit_code=0, stdout="[narrate] source=template 3 steps (fell back ...)")
    assert rec["llm"]["attempts"] == 0 and rec["llm"]["fallback_reason"] == "request failed: boom"
    assert rec["narration"]["retries"] == 0 and rec["narration"]["validation_errors"] == 0


def test_collect_tolerates_a_markers_file_that_is_not_an_object(tmp_path):
    out = make_run_dir(tmp_path / "r1")
    (out / "logs" / "markers.json").write_text("[1, 2]", encoding="utf-8")
    rec = bench.collect(out, bench.parse_driver("template"), SCENARIO, exit_code=0)
    assert rec["verdict"] == "PASS" and rec["failing_step"] is None


def test_collect_opencode_run(tmp_path):
    d = bench.parse_driver("opencode:fake/scripted@http://127.0.0.1:5/v1")
    out = make_run_dir(tmp_path / "r1", with_run_json=False)
    _write(out / "logs" / "narrate-validate.json", {"valid": True, "errors": [], "words": 44, "budget": 156})
    ev = _events(PLAYBOOK, tokens=960, cost=0.0)
    ev["steps_limit"] = 60
    rec = bench.collect(out, d, SCENARIO, n=2, wall_s=61.0, exit_code=0, stdout="{}", events=ev)
    assert rec["verdict"] == "PASS" and rec["failing_stage"] is None
    assert rec["stages"]["narrate"] == 3.0 and rec["stages"]["doctor"] == 1.5     # from the tool-call clock
    oc = rec["opencode"]
    assert oc["tool_calls"] == 8 and oc["kit_tool_calls"] == 8
    assert oc["kit_commands"][:2] == ["doctor", "dryrun"] and len(oc["commands"]) == 8
    assert oc["permission_prompts"] == 0 and oc["denied"] == 0
    assert oc["steps"] == 9 and oc["steps_limit"] == 60 and oc["step_limit_reached"] is False
    assert oc["tokens_total"] == 960 and oc["tokens_in"] == 940 and oc["cost"] == 0.0
    assert oc["narration_written_by_agent"] is False and oc["used_narrate_template"] is True
    assert rec["narration"]["source"] == "template" and rec["narration"]["retries"] == 0
    assert rec["llm"] is None and rec["report"] is None and rec["run"] == 2

    # the agent wrote narration.json itself and needed one validation retry
    cmds = PLAYBOOK[:2] + ["python -m demo_smoke narrate-validate s --out o"] * 2 + PLAYBOOK[4:]
    ev = _events(cmds, wrote_narration=True)
    _write(out / "logs" / "narrate-validate.json", {"valid": False, "errors": ["intro has 50 words (max 45)"]})
    rec = bench.collect(out, d, SCENARIO, wall_s=70.0, exit_code=0, events=ev)
    assert rec["narration"]["source"] == "agent"
    assert rec["narration"]["retries"] == 1 and rec["narration"]["validation_errors"] == 1
    assert rec["opencode"]["tool_calls"] == 9 and rec["opencode"]["kit_tool_calls"] == 8


def test_collect_opencode_verdicts(tmp_path):
    d = bench.parse_driver("opencode:fake/scripted")
    # a kit command exited 2 (dryrun FAIL) and the agent stopped
    out = make_run_dir(tmp_path / "fail", with_run_json=False, verdict="FAIL", stage="dryrun", video=False)
    ev = _events(PLAYBOOK[:2])
    ev["failed_tool_calls"] = [{"name": "bash", "command": PLAYBOOK[1], "exit_code": 2, "error": None}]
    rec = bench.collect(out, d, SCENARIO, wall_s=9.0, exit_code=0, events=ev)
    assert rec["verdict"] == "FAIL" and rec["failing_stage"] == "dryrun" and rec["failing_step"] == "ask"
    # verify failed
    out = make_run_dir(tmp_path / "verify", with_run_json=False)
    _write(out / "logs" / "verify.json", {"pass": False, "duration": 80.0,
                                          "checks": [{"name": "duration", "pass": False, "detail": "too long"}]})
    rec = bench.collect(out, d, SCENARIO, wall_s=9.0, exit_code=0, events=_events(PLAYBOOK))
    assert rec["verdict"] == "FAIL" and rec["failing_stage"] == "verify"
    assert rec["video"]["checks"] == [{"name": "duration", "pass": False}]
    # opencode itself failed (non-zero exit, nothing produced)
    out = tmp_path / "boom"
    out.mkdir()
    rec = bench.collect(out, d, SCENARIO, wall_s=1.0, exit_code=1, events=_events([]), error="opencode exited with code 1")
    assert rec["verdict"] == "ERROR" and rec["failing_stage"] == "opencode"
    assert rec["opencode"]["tool_calls"] == 0
    # an `error` event unrelated to the pipeline (title generation hit a dead provider) after verify passed:
    # the video was delivered, so the run is PASS and the message is kept for the notes
    out = make_run_dir(tmp_path / "titled", with_run_json=False)
    ev = _events(PLAYBOOK)
    ev["errors"] = ["ProviderModelNotFoundError: ollama/qwen3-coder:30b"]
    ev["final_status"] = "error"
    rec = bench.collect(out, d, SCENARIO, wall_s=50.0, exit_code=0, events=ev,
                        error="ProviderModelNotFoundError: ollama/qwen3-coder:30b")
    assert rec["verdict"] == "PASS" and rec["failing_stage"] is None
    assert rec["error"] == "ProviderModelNotFoundError: ollama/qwen3-coder:30b"
    rows = bench_report.aggregate([rec])
    assert "r1 PASS with an error reported: ProviderModelNotFoundError" in rows[0]["notes"]
    assert any("passed although the session reported an error" in d for d in bench_report.differences(rows, [rec]))
    # the agent stopped early without an error (step limit) -> ERROR at the last kit command
    out = make_run_dir(tmp_path / "early", with_run_json=False, video=False)
    ev = _events(PLAYBOOK[:5])
    ev["step_limit_reached"] = True
    rec = bench.collect(out, d, SCENARIO, wall_s=1.0, exit_code=0, events=ev)
    assert rec["verdict"] == "ERROR" and rec["failing_stage"] == "synth"
    assert rec["opencode"]["step_limit_reached"] is True
    # killed on the bench timeout after verify had passed (e.g. the title agent hung on a provider): ERROR,
    # never PASS - its wall time is the timeout, and must not enter the PASS mean
    out = make_run_dir(tmp_path / "hung", with_run_json=False)
    rec = bench.collect(out, d, SCENARIO, wall_s=3600.0, exit_code=None, events=_events(PLAYBOOK),
                        error="timed out after 3600 s")
    assert rec["verdict"] == "ERROR" and rec["failing_stage"] == "verify" and rec["error"] == "timed out after 3600 s"
    ok = bench.collect(make_run_dir(tmp_path / "ok", with_run_json=False), d, SCENARIO, n=2, wall_s=600.0,
                       exit_code=0, events=_events(PLAYBOOK))
    row = bench_report.aggregate([rec, ok])[0]
    assert row["verdict"] == "PASS 1/2" and row["total_minutes"] == 10.0 and row["pass_minutes"] == 10.0
    assert "r1 ERROR at verify: timed out after 3600 s" in row["notes"]


def test_opencode_summary_uses_parser_and_fallback(monkeypatch):
    sample = bench.KIT / "tests" / "fixtures" / "opencode-events.sample.jsonl"
    if sample.is_file():
        summ = bench.opencode_summary(sample.read_text(encoding="utf-8"))
        assert summ["parser"] == "opencode_events" and summ["tool_calls"] >= 8
        assert summ["kit_commands"][0] == "doctor" and summ["stages"]["dryrun"] is not None
    real_import = bench.importlib.import_module

    def missing(name, *a, **k):
        if name == "demo_smoke.opencode_events":
            raise ImportError(name)
        return real_import(name, *a, **k)

    monkeypatch.setattr(bench.importlib, "import_module", missing)
    text = "\n".join([json.dumps({"type": "tool_use", "part": {"state": {"input": {"command": c}}}}) for c in PLAYBOOK]
                     + ['{"type":"text","part":{"text":"SMOKE DONE"}}'])
    fb = bench.opencode_summary(text)
    assert fb["parser"] == "fallback" and fb["tool_calls"] == 8
    assert fb["kit_commands"] == ["doctor", "dryrun", "narrate-template", "narrate-validate", "synth", "record",
                                  "edit", "verify"]
    assert fb["used_narrate_template"] is True and fb["tokens_total"] is None


def test_run_driver_runs_the_kit_as_a_subprocess(tmp_path):
    """``run_driver`` really spawns ``python -m demo_smoke run``: a scenario whose upload file is
    missing is rejected as bad input (exit 4) before a browser is needed, and lands as ERROR."""
    scen = tmp_path / "s.json"
    scen.write_text(json.dumps(SCENARIO), encoding="utf-8")          # a.pdf does not exist next to it
    d = bench.parse_driver("template")
    rec = bench.run_driver(d, 1, scen, SCENARIO, tmp_path / "bench", tts="tone", headless=True, ref=None,
                           timeout=120, opencode_bin=None)
    assert rec["exit_code"] == 4 and rec["verdict"] == "ERROR", rec
    assert "code 4" in rec["error"]
    assert rec["argv"][1:4] == ["-m", "demo_smoke", "run"] and "--narration" in rec["argv"]
    run = tmp_path / "bench" / "runs" / "template" / "r1"
    assert (run / "bench.json").is_file()
    assert json.loads((run / "bench.json").read_text(encoding="utf-8"))["verdict"] == "ERROR"
    assert "a.pdf" in (run / "logs" / "bench-stderr.txt").read_text(encoding="utf-8")
    assert rec["started"] <= rec["finished"] and rec["wall_s"] >= 0


def _alive(pid: int) -> bool:
    """POSIX: is ``pid`` still a running (not zombie) process?"""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        state = (Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split() or ["?"])[0]
    except OSError:
        return True
    return state not in ("Z", "X")


@pytest.mark.skipif(os.name == "nt", reason="the grandchild liveness probe below is POSIX-only")
def test_run_process_timeout_kills_the_whole_process_tree(tmp_path):
    """A timed-out driver takes its children with it (OpenCode -> python -> Chrome/ffmpeg in real runs):
    the grandchild sleeper must be gone, and the partial output must still be collected."""
    child = ("import subprocess, sys, time\n"
             "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
             "print('grandchild', p.pid, flush=True)\n"
             "time.sleep(120)\n")
    t0 = time.time()
    code, out, _err, error = bench._run_process([sys.executable, "-c", child], bench._base_env(), tmp_path, 2,
                                                tmp_path / "logs")
    assert time.time() - t0 < 30
    assert code is None and error == "timed out after 2 s"
    assert out.startswith("grandchild ") and (tmp_path / "logs" / "bench-stdout.txt").read_text().startswith("grandchild")
    pid = int(out.split()[1])
    deadline = time.time() + 10
    while _alive(pid) and time.time() < deadline:
        time.sleep(0.1)
    assert not _alive(pid), f"grandchild {pid} survived the timeout"


def test_run_process_decodes_utf8_and_starts_a_new_group(tmp_path):
    script = ("import os, sys; sys.stdout.buffer.write('caf\u00e9 \u2014 ok\\n'.encode('utf-8')); "
              "print(os.getpgid(0) == os.getpid() if hasattr(os, 'getpgid') else True)")
    code, out, _err, error = bench._run_process([sys.executable, "-c", script], bench._base_env(), tmp_path, 30,
                                                tmp_path / "logs")
    assert code == 0 and error is None
    assert out.splitlines() == ["caf\u00e9 \u2014 ok", "True"]       # UTF-8 whatever the locale; own process group


def test_run_driver_reports_a_missing_opencode_binary(tmp_path):
    d = bench.parse_driver("opencode:fake/scripted")
    rec = bench.run_driver(d, 1, tmp_path / "s.json", SCENARIO, tmp_path / "bench", tts="tone", headless=True,
                           ref=None, timeout=5, opencode_bin=None)
    assert rec["verdict"] == "ERROR" and "opencode binary not found" in rec["error"]
    assert rec["opencode"]["tool_calls"] == 0 and rec["argv"] == []


# ----------------------------------------------------------------------------- report


def _bench_dict(tmp_path: Path, runs: list[dict], baseline: list[dict] | None = None) -> dict:
    return {"version": "x", "scenario": "/s/fixture-pass.json", "name": SCENARIO["name"], "slug": "fixture-pass",
            "started": "2026-09-02T10:00:00+00:00", "finished": "2026-09-02T10:05:00+00:00", "wall_s": 300.0,
            "out": str(tmp_path), "drivers": [{"spec": r["driver"]} for r in runs], "repeat": 1,
            "args": {"tts": "tone", "headless": True}, "baseline": baseline or [], "runs": runs,
            "screen_recordings": [], "interrupted": False}


def test_aggregate_and_report(tmp_path):
    t = bench.parse_driver("template")
    o = bench.parse_driver("opencode:fake/scripted@http://127.0.0.1:5/v1")
    r1 = bench.collect(make_run_dir(tmp_path / "runs" / "template" / "r1"), t, SCENARIO, n=1, wall_s=60.0, exit_code=0)
    r2 = bench.collect(make_run_dir(tmp_path / "runs" / "template" / "r2", verdict="FAIL", stage="dryrun", video=False),
                       t, SCENARIO, n=2, wall_s=30.0, exit_code=2)
    o1 = bench.collect(make_run_dir(tmp_path / "runs" / "opencode-fake-scripted" / "r1", with_run_json=False), o,
                       SCENARIO, n=1, wall_s=120.0, exit_code=0, events=_events(PLAYBOOK, tokens=1000, cost=0.5))
    runs = [r1, r2, o1]
    rows = bench_report.aggregate(runs)
    assert [r["slug"] for r in rows] == ["template", "opencode-fake-scripted"]
    trow, orow = rows
    # minutes: the PASS run only (60 s -> 1.0), the mean over both runs kept apart
    assert trow["verdict"] == "PASS 1/2" and trow["runs"] == 2 and trow["passed_runs"] == 1
    assert trow["total_minutes"] == 1.0 and trow["pass_minutes"] == 1.0 and trow["all_runs_minutes"] == 0.8
    assert trow["run_minutes"] == [1.0, 0.5]
    assert trow["tool_calls"] is None and trow["narration_source"] == "template"
    assert trow["narration_sources"] == {"template": 2}
    assert "r2 FAIL at dryrun/ask" in trow["notes"] and "min per run: r1 1.0, r2 0.5" in trow["notes"]
    # every mean covers the PASS runs (r1 only), so each count is 1 of 2 and the cells say (1/2)
    assert trow["mean_over"] == "PASS"
    assert trow["counts"]["video_seconds"] == 1 and trow["counts"]["narration_words"] == 1
    assert orow["verdict"] == "PASS" and orow["tool_calls"] == 8 and orow["tokens_total"] == 1000 and orow["cost"] == 0.5
    assert orow["pass_minutes"] == 2.0 and orow["kit_tool_calls"] == 8 and "min per run" not in orow["notes"]
    assert orow["references_on_screen"] == 0.6 and orow["narration_words"] == 44 and orow["video_seconds"] == 24.5

    baseline = bench_report.load_baseline(bench.KIT / "bench" / "baseline.example.json")
    assert baseline[0]["driver"] == "manual" and baseline[0]["model"] == "codex (cloud)"
    assert baseline[0]["total_minutes"] == 95 and baseline[0]["tool_calls"] is None
    b = _bench_dict(tmp_path, runs, baseline)
    report_path, json_path = bench_report.write(tmp_path, b)
    md = report_path.read_text(encoding="utf-8")
    assert "<html" not in md and "<table" not in md                     # plain markdown
    head = "| driver | model | verdict | total min | narration | tool calls | words | on-screen refs |"
    assert head in md
    assert trow["label"] == "template" and orow["label"] == "opencode:fake/scripted"      # no @base-url in prose
    assert "| template | - | PASS 1/2 | 1.0 | template | - | 44 (1/2) | 60% (1/2) | 0 (1/2) | 24.5 (1/2) | - |" in md
    assert "| opencode:fake/scripted | fake/scripted | PASS | 2.0 | template | 8 | 44 | 60% | 0 | 24.5 | 1,000 tok / $0.5000 |" in md
    assert "averaged over that driver's PASS runs only" in md and "`(k/n)`" in md
    assert "127.0.0.1:5" not in md.split("## Results", 1)[1]          # the override URL stays in the header only
    assert "| manual | codex (cloud) | PASS | 95 | cloud model | - | - | - | - | - | - | manual entry 2026-08-31; first run incl. installs; Loom link |" in md
    assert "## What differed" in md
    assert "template: 1/2 run(s) did not pass (FAIL; stage dryrun)." in md
    assert ("opencode:fake/scripted ran 8 kit commands per run, at or above the playbook minimum of 7 "
            "(8 tool calls per run, including file reads and narration writes; mean over its PASS runs)") in md
    # the template runs no model: it is the pipeline-only baseline, never ranked as the fastest driver
    assert "Fastest passing" not in md                                 # one model driver: nothing to rank
    assert "Pipeline-only baseline: template passed in 1.0 min (no model ran" in md
    assert ('Manual baseline codex (cloud) took 95 min (PASS) per its own notes ("first run incl. installs; Loom link"); '
            "the fastest passing model driver here took 2.0 min (opencode:fake/scripted). The manual figure is what a "
            "person wrote down, not a bench measurement") in md
    assert "On-screen references" not in md.split("## What differed", 1)[1].split("## Runs", 1)[0]   # template text only
    assert "[report.md](runs/template/r1/report.md)" in md
    assert "[video](runs/template/r1/final/fixture-pass.mp4)" in md
    assert "[events](runs/opencode-fake-scripted/r1/logs/opencode-events.json)" in md
    assert "## Appendix: per-run rows" in md and "| template | 2 | FAIL |" in md
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert saved["rows"][0]["verdict"] == "PASS 1/2" and len(saved["runs"]) == 3
    assert saved["differences"] and saved["generated_at"]
    assert bench_report.validate(saved) == []


def test_differences_rank_speed_on_pass_runs_and_refs_on_model_narration_only():
    fast_error = {"driver": "opencode:x/dead", "label": "opencode:x/dead", "kind": "opencode", "slug": "dead",
                  "runs": 1, "passed_runs": 0, "verdict": "ERROR", "verdicts": ["ERROR"], "total_minutes": 0.0,
                  "pass_minutes": None, "references_on_screen": 1.0, "narration_source": "template",
                  "narration_sources": {"template": 1}}
    slow_pass = {"driver": "opencode:x/ok", "label": "opencode:x/ok", "kind": "opencode", "slug": "ok", "runs": 3,
                 "passed_runs": 2, "verdict": "PASS 2/3", "verdicts": ["PASS", "ERROR", "PASS"], "total_minutes": 5.2,
                 "pass_minutes": 5.2, "all_runs_minutes": 23.4, "references_on_screen": 0.5,
                 "narration_source": "agent", "narration_sources": {"agent": 3}, "tool_calls": 8.333,
                 "kit_tool_calls": 5.333}
    slower_pass = {"driver": "opencode:x/slow", "label": "opencode:x/slow", "kind": "opencode", "slug": "slow",
                   "runs": 1, "passed_runs": 1, "verdict": "PASS", "verdicts": ["PASS"], "total_minutes": 9.1,
                   "pass_minutes": 9.1, "references_on_screen": 0.4, "narration_source": "agent",
                   "narration_sources": {"agent": 1}, "tool_calls": 9, "kit_tool_calls": 8}
    template = {"driver": "template", "label": "template", "kind": "template", "slug": "template", "runs": 1,
                "passed_runs": 1, "verdict": "PASS", "verdicts": ["PASS"], "total_minutes": 4.0, "pass_minutes": 4.0,
                "references_on_screen": 1.0, "narration_source": "template", "narration_sources": {"template": 1}}
    runs = [{"slug": "dead", "verdict": "ERROR", "failing_stage": "opencode"},
            {"slug": "ok", "verdict": "PASS"}, {"slug": "ok", "verdict": "ERROR", "failing_stage": "record"},
            {"slug": "ok", "verdict": "PASS"}, {"slug": "slow", "verdict": "PASS"}, {"slug": "template", "verdict": "PASS"}]
    manual = [{"driver": "manual", "model": "codex", "verdict": "PASS", "total_minutes": 95}]
    text = "\n".join(bench_report.differences([fast_error, slow_pass, slower_pass, template], runs, manual))
    # only model drivers are ranked: the 4.0-min template (no model) is quoted apart as the pipeline baseline
    assert "Fastest passing model driver: opencode:x/ok at 5.2 min; slowest passing: opencode:x/slow at 9.1 min" in text
    assert "Pipeline-only baseline: template passed in 4.0 min" in text
    assert "0.0 min" not in text                                    # the instant ERROR is never "fastest"
    assert "the fastest passing model driver here took 5.2 min (opencode:x/ok)" in text
    assert "opencode:x/ok ran 5.333 kit commands per run, below the playbook minimum of 7 (8.333 tool calls per run" in text
    assert "in total" not in text                                   # the figure is a per-run mean
    only_template = "\n".join(bench_report.differences([fast_error, template], runs, manual))
    assert "No model driver passed, so there is no fastest model driver to name." in only_template
    assert "no model driver passed here (the pipeline-only template baseline took 4.0 min)" in only_template
    assert "On-screen references of model-authored narration" in text and "opencode:x/ok 50%" in text
    assert "template 100%" not in text and "opencode:x/dead 100%" not in text
    nothing = bench_report.differences([fast_error], runs[:1], [{"driver": "manual", "verdict": "PASS",
                                                                 "total_minutes": 95}])
    assert any("No driver passed, so there is no fastest driver" in d for d in nothing)
    assert any("no automated driver passed here" in d for d in nothing)
    assert bench_report._verdict_label(["FAIL", "ERROR"]) == "PASS 0/2 (FAIL 1, ERROR 1)"
    assert bench_report._verdict_label(["PASS", "FAIL", "ERROR", "FAIL"]) == "PASS 1/4 (FAIL 2, ERROR 1)"
    assert bench_report._verdict_label(["PASS", "FAIL"]) == "PASS 1/2"
    assert bench_report._verdict_label(["ERROR", "ERROR"]) == "ERROR"


def test_validate_flags_problems():
    assert "missing top-level key 'runs'" in bench_report.validate({})[0:9]
    good = {"scenario": "s", "started": "", "finished": "", "drivers": [], "repeat": 1, "runs": [], "rows": [],
            "differences": [], "baseline": []}
    assert bench_report.validate(good) == []
    bad = dict(good, runs=[{"kind": "opencode", "verdict": "MAYBE", "wall_s": "x", "stages": {}, "narration": {},
                            "opencode": None}], rows=[{"driver": "x", "verdict": "PASS", "slug": "ghost"}])
    errs = bench_report.validate(bad)
    assert any("verdict 'MAYBE'" in e for e in errs)
    assert any("wall_s" in e for e in errs) and any("stages" in e for e in errs)
    assert any("opencode block missing" in e for e in errs)
    assert any("slug 'ghost' has no runs" in e for e in errs)
    assert bench_report.validate("nope") == ["bench.json must be an object"]


def test_load_baseline_rejects_bad_files(tmp_path):
    p = tmp_path / "b.json"
    p.write_text('{"x": 1}', encoding="utf-8")
    with pytest.raises(TypeError):
        bench_report.load_baseline(p)
    p.write_text('[1]', encoding="utf-8")
    with pytest.raises(TypeError):
        bench_report.load_baseline(p)
    p.write_text('{"entries": [{"driver": "manual", "model": "m", "verdict": "pass", "total_minutes": 12}]}',
                 encoding="utf-8")
    rows = bench_report.baseline_rows(bench_report.load_baseline(p))
    assert rows[0]["manual"] is True and rows[0]["verdict"] == "PASS" and rows[0]["notes"] == "manual entry"
    # optional numbers are rendered in the bench's units: a hand-written 80 (percent) would show as 8000%
    for bad in ('[{"driver": "manual", "references_on_screen": 80}]', '[{"driver": "manual", "video_seconds": -1}]',
                '[{"driver": "manual", "total_minutes": "95"}]', '[{"driver": "manual", "cost": true}]'):
        p.write_text(bad, encoding="utf-8")
        with pytest.raises((TypeError, ValueError)):
            bench_report.load_baseline(p)
    p.write_text('[{"driver": "manual", "references_on_screen": 0.8, "video_seconds": 60, "tokens_total": null}]',
                 encoding="utf-8")
    assert bench_report.load_baseline(p)[0]["references_on_screen"] == 0.8


def test_bench_cli_bad_input(tmp_path, capsys):
    scen = tmp_path / "s.json"
    scen.write_text(json.dumps(SCENARIO), encoding="utf-8")
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4\n")
    assert bench.main([str(scen), "--out", str(tmp_path / "b"), "--driver", "codex:x"]) == 4
    assert "unknown driver kind" in capsys.readouterr().err
    assert bench.main([str(tmp_path / "missing.json"), "--out", str(tmp_path / "b")]) == 4
    assert bench.main([str(scen), "--out", str(tmp_path / "b"), "--repeat", "0"]) == 4
    assert bench.main([str(scen), "--out", str(tmp_path / "b"), "--ref", str(tmp_path / "no.wav")]) == 4
    bad_baseline = tmp_path / "bl.json"
    bad_baseline.write_text("[1]", encoding="utf-8")
    assert bench.main([str(scen), "--out", str(tmp_path / "b"), "--baseline", str(bad_baseline)]) == 4
    assert not (tmp_path / "b" / "report.md").exists()
    # argparse usage errors are bad input (4), not the bench's "some run FAIL" code (2)
    assert bench.main([str(scen), "--out", str(tmp_path / "b"), "--no-such-flag"]) == 4
    assert bench.main([str(scen)]) == 4
    assert "usage" in capsys.readouterr().err


def test_register_adds_bench_subcommand():
    import argparse
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd")
    run_map: dict = {}
    bench.register(sub, run_map)
    assert run_map == {"bench": bench.cmd_bench}
    ns = p.parse_args(["bench", "s.json", "--out", "o", "--driver", "template", "--driver", "opencode:a/b",
                       "--repeat", "2", "--tts", "tone", "--headless", "--baseline", "b.json", "--timeout-s", "9",
                       "--llm-timeout", "7", "--record-screen", "--meta-narrate", "--meta-from-clips", "a.mp4", "b.mp4"])
    assert ns.driver == ["template", "opencode:a/b"] and ns.repeat == 2 and ns.timeout_s == 9 and ns.llm_timeout == 7
    assert p.parse_args(["bench", "s.json", "--out", "o"]).llm_timeout == bench.DEFAULT_LLM_TIMEOUT_S == 180
    assert ns.meta_from_clips == ["a.mp4", "b.mp4"] and ns.record_screen and ns.meta_narrate
    assert ns.fn is bench.cmd_bench
