"""argparse front end: ``python -m demo_smoke <cmd> ...``.

Exit codes: 0 ok, 2 feature failed (smoke FAIL / failed checks), 3 pipeline or
tooling error, 4 bad input, 130 interrupted (Ctrl-C).  Every command prints one summary line to stdout
and writes ``<out>/logs/<cmd>.json``.  Heavy modules (chrome, drive, capture,
edit, verify, chatterbox) are imported lazily so ``doctor``/``narrate-*`` work
without them.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import time
from pathlib import Path

from . import (
    __version__,
    bench,
    bench_meta,
    dotenv,
    onboard_audio,
    onboard_scenario,
    opencode_events,
)
from .env import Paths
from .scenario import ScenarioError

EXIT_OK = 0
EXIT_FAIL = 2
EXIT_ERROR = 3
EXIT_BAD_INPUT = 4
DEFAULT_OUT = "demo-output"
TTS_CHOICES = ("auto", "turbo", "nano", "classic", "tone")
CAPTURE_CHOICES = ("screencast", "screen")
VOICE_CHECK_TEXT = ("This is a short voice check for the demo narration. "
                    "If you can hear this clearly, the cloned voice is ready.")


class PipelineError(RuntimeError):
    """Expected tooling failure surfaced as a one-line message (exit 3)."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # bad input -> exit 4, one line, no traceback
        self.print_usage(sys.stderr)
        print(f"error: {message}", file=sys.stderr)
        raise SystemExit(EXIT_BAD_INPUT)


def _mod(name: str):
    """Lazy import of a sibling module (``sys.modules`` first, so tests can inject fakes)."""
    return importlib.import_module(f"demo_smoke.{name}")


def _say(msg: str) -> None:
    print(msg, flush=True)


def _err(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr, flush=True)


def _log(paths: Paths, cmd: str, data: dict) -> Path:
    p = paths.logs / f"{cmd}.json"
    p.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return p


def _json_safe(scenario: dict) -> dict:
    return {k: (str(v) if isinstance(v, Path) else v) for k, v in scenario.items()}


def _load_scenario(path: str, paths: Paths, check_files: bool = False) -> dict:
    scen = _mod("scenario").load(path, check_files=check_files)
    (paths.logs / "scenario.json").write_text(
        json.dumps(_json_safe(scen), indent=2), encoding="utf-8")
    return scen


def _scenario_for(args, paths: Paths, check_files: bool = False) -> dict:
    """Positional SCENARIO if given, else the copy saved by an earlier command."""
    given = getattr(args, "scenario", None)
    if given:
        return _load_scenario(given, paths, check_files)
    saved = paths.logs / "scenario.json"
    if not saved.is_file():
        raise PipelineError(
            f"no scenario: pass SCENARIO or run dryrun/record first (missing {saved})")
    data = json.loads(saved.read_text(encoding="utf-8"))
    if "_dir" in data:
        data["_dir"] = Path(data["_dir"])
    return data


def _read_json(p: Path, hint: str) -> dict:
    if not p.is_file():
        raise PipelineError(f"{p} not found: {hint}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise PipelineError(f"{p} is not valid JSON: {e}") from None


def _write_narration(paths: Paths, narr: dict) -> Path:
    p = paths.audio / "narration.json"
    p.write_text(json.dumps(narr, indent=2, ensure_ascii=False), encoding="utf-8")
    return p


def _step_summary(steps: list[dict]) -> str:
    return " ".join(f"{s.get('id')}={s.get('status')}" for s in steps)


def _bad_input(paths: Paths, cmd: str, msg: str) -> int:
    _log(paths, cmd, {"error": msg, "exit_code": EXIT_BAD_INPUT})
    _err(msg)
    return EXIT_BAD_INPUT


def _record_failure(m: dict) -> str | None:
    """Why a recording is not usable: a login/pipeline error or any step that did not PASS."""
    if m.get("error"):
        return str(m["error"])
    bad = [f"{s.get('id')}={s.get('status')}" for s in m.get("steps", []) if s.get("status") != "PASS"]
    if bad or m.get("verdict", "PASS") != "PASS":
        return "steps failed during recording: " + (", ".join(bad) or "no step passed")
    return None


# --------------------------------------------------------------------------- commands


def cmd_doctor(args) -> int:
    env = _mod("env")
    paths = Paths(args.out)
    rep = env.detect(args.base_url, args.model, timeout=args.timeout)
    llm = rep.get("llm") or {}
    tc = llm.get("tool_call") or {}
    bits = [
        f"ffmpeg={'ok' if rep['ffmpeg'] else 'MISSING'}",
        f"chrome={'ok' if rep['chrome'] else 'MISSING'}",
        f"torch={rep['torch_device']}",
        f"chatterbox={'yes' if rep['chatterbox'] else 'no'}",
    ]
    if rep.get("tts_auto"):
        bits.append(f"tts_auto={rep['tts_auto']}")
        bits.append(f"tts_ready={'yes' if rep.get('tts_ready') else 'NO'}")
    if args.base_url:
        bits.append(f"llm={'reachable' if llm.get('reachable') else 'UNREACHABLE'}")
        if args.model and tc:
            bits.append(f"tool_call={'PASS' if tc.get('pass') else 'FAIL'}")
    bits.append(f"opencode={'ok' if rep.get('opencode') else 'MISSING'}")
    sac = rep.get("smart_app_control")
    if sac is not None:   # Windows only
        bits.append(f"smart_app_control={sac.upper() if sac == 'on' else sac}")
    endpoints = rep.get("local_endpoints") or []
    up = [e["name"] for e in endpoints if e.get("reachable")]
    bits.append("local_llm=" + (",".join(up) if up else "none"))
    problems = [k for k in ("ffmpeg", "chrome") if not rep.get(k)]
    if args.base_url and not llm.get("reachable"):
        problems.append("llm unreachable")
    if args.model and tc and not tc.get("pass"):
        problems.append("tool_call FAIL")
    if sac == "on":   # unsigned .pyd files (torch, pandas, librosa) cannot load; the hint says how to turn it off
        problems.append("smart_app_control ON")
    ok = not problems
    if not ok:   # contract: every exit-3 log carries "error" and "exit_code" (plus the report)
        rep["error"] = "doctor: PROBLEMS " + ", ".join(problems)
        rep["exit_code"] = EXIT_ERROR
    _log(paths, "doctor", rep)
    _say(f"doctor: {'ok' if ok else 'PROBLEMS'} " + " ".join(bits)
         + f" (details: {paths.logs / 'doctor.json'})")
    if rep.get("tts_advice"):
        _say(f"  tts: {rep['tts_advice']}")
    for e in endpoints:
        if e.get("reachable"):
            models = e.get("models") or []
            shown = ", ".join(models[:8]) + (f", ... ({len(models)} total)" if len(models) > 8 else "")
            _say(f"  llm: {e['name']} {e['base_url']} reachable: "
                 + (shown if models else f"no models listed ({e.get('error') or 'empty list'})"))
        else:
            _say(f"  llm: {e['name']} {e['base_url']} not running")
    for h in rep.get("hints", []):
        _say(f"  hint: {h}")
    return EXIT_OK if ok else EXIT_ERROR


def cmd_check_model(args) -> int:
    llm = _mod("llm")
    paths = Paths(args.out)
    if args.list:
        try:
            models = llm.list_models(args.base_url, timeout=min(args.timeout, 30))
        except llm.LLMError as e:
            msg = f"cannot list models at {args.base_url}: {e}"
            _log(paths, "check-model", {"list": True, "base_url": args.base_url, "models": [],
                                        "pass": False, "detail": str(e),
                                        "error": f"check-model: {msg}", "exit_code": EXIT_ERROR})
            _err(msg)
            return EXIT_ERROR
        _log(paths, "check-model", {"list": True, "base_url": args.base_url, "models": models})
        _say(f"check-model: {len(models)} model(s) at {args.base_url}"
             + ("" if models else " (none loaded: load one in the server UI, or `ollama pull`)"))
        for mid in models:
            _say(f"  {mid}")
        return EXIT_OK
    if not args.model:
        _log(paths, "check-model", {"pass": False, "detail": "no model given", "base_url": args.base_url,
                                    "model": None, "error": "check-model: --model NAME is required "
                                    "(or --list to see the ids the server offers)",
                                    "exit_code": EXIT_BAD_INPUT})
        _err("check-model: --model NAME is required (or DEMO_SMOKE_MODEL, or --list to see the ids)")
        return EXIT_BAD_INPUT
    if not llm.reachable(args.base_url):
        msg = f"LLM endpoint not reachable at {args.base_url} (is the server running?)"
        _log(paths, "check-model", {"pass": False, "detail": "endpoint unreachable",
                                    "base_url": args.base_url, "model": args.model,
                                    "error": f"check-model: {msg}", "exit_code": EXIT_ERROR})
        _err(msg)
        return EXIT_ERROR
    res = llm.probe_tool_call(args.base_url, args.model, timeout=args.timeout)
    res.update({"base_url": args.base_url, "model": args.model})
    _log(paths, "check-model", res)
    _say(f"check-model: {'PASS' if res['pass'] else 'FAIL'} model={args.model} {res['detail']}")
    return EXIT_OK if res["pass"] else EXIT_FAIL


def cmd_prefetch(args) -> int:
    tts = _mod("tts")
    env = _mod("env")
    paths = Paths(args.out)
    dev = env.torch_device()
    if dev == "none":
        raise PipelineError("torch is not installed; pip install -r requirements-tts.txt")
    device = "cpu" if dev == "cpu" else ("cuda" if dev == "rocm" else dev)
    backend = tts.resolve_backend(args.tts)   # "auto" -> what run/synth --tts auto will use here
    t0 = time.time()
    tts.load_model(backend, device=device, online=True)
    cache = env.hf_cache_dir()
    _log(paths, "prefetch", {"tts": args.tts, "backend": backend, "device": device, "hf_cache": cache,
                             "seconds": round(time.time() - t0, 1)})
    _say(f"prefetch: ok tts={args.tts}" + (f" (resolved to {backend})" if backend != args.tts else "")
         + f" weights cached under {cache}")
    return EXIT_OK


def cmd_voice_check(args) -> int:
    tts = _mod("tts")
    paths = Paths(args.out)
    tts.set_offline_env(args.online)   # before anything can import chatterbox / huggingface_hub
    backend = tts.resolve_backend(args.tts)
    ref = Path(args.ref) if args.ref else None
    if ref is not None and not ref.is_file():
        return _bad_input(paths, "voice-check", f"reference voice not found: {ref}")
    t0 = time.time()
    wav, sr = tts.synthesize(VOICE_CHECK_TEXT, ref, backend, online=args.online)
    p = tts.write_wav(paths.audio / "voice_check.wav", wav, sr)
    stats = tts.audio_stats(wav, sr)
    data = {"backend": backend, "ref": str(ref) if ref else None, "path": str(p), "sr": sr,
            "seconds_to_synthesize": round(time.time() - t0, 2), **stats}
    _log(paths, "voice-check", data)
    ok = not stats["silent"] and not stats["clipped"]
    _say(f"voice-check: {'ok' if ok else 'PROBLEM'} backend={backend} {p} "
         f"duration={stats['duration']}s peak={stats['peak_dbfs']}dBFS "
         f"rms={stats['rms_dbfs']}dBFS silent={stats['silent']} clipped={stats['clipped']}")
    return EXIT_OK if ok else EXIT_FAIL


def cmd_dryrun(args) -> int:
    paths = Paths(args.out)
    scen = _load_scenario(args.scenario, paths, check_files=True)
    res = _mod("drive").dryrun(scen, paths.out, headless=args.headless)
    _log(paths, "dryrun", res)
    _say(f"dryrun: {res['verdict']} {_step_summary(res.get('steps', []))} "
         f"(attempts={res.get('attempts', 1)}, {paths.logs / 'smoke-results.md'})")
    return EXIT_OK if res["verdict"] == "PASS" else EXIT_FAIL


def cmd_narrate_template(args) -> int:
    narration = _mod("narration")
    paths = Paths(args.out)
    scen = _load_scenario(args.scenario, paths)
    narr = narration.template(scen)
    p = _write_narration(paths, narr)
    total = sum(narration.words(s["text"]) for s in narr["steps"]) \
        + narration.words(narr["intro"]) + narration.words(narr["outro"])
    _log(paths, "narrate-template", {"path": str(p), "words": total, "narration": narr})
    _say(f"narrate-template: ok {len(narr['steps'])} steps, {total} words -> {p}")
    return EXIT_OK


def cmd_narrate_llm(args) -> int:
    narration = _mod("narration")
    paths = Paths(args.out)
    scen = _load_scenario(args.scenario, paths)
    detail = narration.from_llm_detail(scen, args.base_url, args.model, timeout=args.timeout)
    narr, source, note = detail["narration"], detail["source"], detail["note"]
    p = _write_narration(paths, narr)
    total = sum(narration.words(s["text"]) for s in narr["steps"]) \
        + narration.words(narr["intro"]) + narration.words(narr["outro"])
    _log(paths, "narrate-llm", {"path": str(p), "source": source, "note": note,
                                "model": args.model, "base_url": args.base_url,
                                "attempts": detail["attempts"], "problems": detail["problems"],
                                "fallback": detail["fallback"], "fallback_reason": detail["fallback_reason"],
                                "words": total, "narration": narr})
    _say(f"narrate-llm: ok source={source} {total} words -> {p} ({note})")
    return EXIT_OK


def cmd_narrate_validate(args) -> int:
    narration = _mod("narration")
    paths = Paths(args.out)
    scen = _scenario_for(args, paths)
    if args.max_seconds:
        scen["max_length_seconds"] = args.max_seconds
    p = paths.audio / "narration.json"
    budget = narration.word_budget(scen)
    if not p.is_file():
        raise PipelineError(f"{p} not found: run narrate-template or narrate-llm first")
    try:
        narr = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        # The likeliest small-model mistake (code fence, trailing comma, prose around the
        # object): treat it like any other invalid narration so the fix-once / template path applies.
        errors = [f"not valid JSON: {e}"]
        _log(paths, "narrate-validate", {"path": str(p), "valid": False, "errors": errors, "words": 0,
                                         "budget": budget, "error": f"narrate-validate: INVALID {p}: {errors[0]}",
                                         "exit_code": EXIT_BAD_INPUT})
        _say(f"narrate-validate: INVALID {p}: {errors[0]}")
        return EXIT_BAD_INPUT
    errors = narration.validate(narr, scen)
    total = 0
    if isinstance(narr, dict):
        total = sum(narration.words(s.get("text", "")) for s in narr.get("steps", [])
                    if isinstance(s, dict)) \
            + narration.words(narr.get("intro", "")) + narration.words(narr.get("outro", ""))
    log = {"path": str(p), "valid": not errors, "errors": errors, "words": total, "budget": budget}
    if errors:
        log["error"] = f"narrate-validate: INVALID {p}: " + "; ".join(errors)
        log["exit_code"] = EXIT_BAD_INPUT
    _log(paths, "narrate-validate", log)
    if errors:
        _say(f"narrate-validate: INVALID {p}: " + "; ".join(errors))
        return EXIT_BAD_INPUT
    _say(f"narrate-validate: ok {total}/{budget} words, {len(narr['steps'])} steps")
    return EXIT_OK


def cmd_synth(args) -> int:
    tts = _mod("tts")
    paths = Paths(args.out)
    tts.set_offline_env(args.online)   # before anything can import chatterbox / huggingface_hub
    backend = tts.resolve_backend(args.tts)
    ref = Path(args.ref) if args.ref else None
    if ref is not None and not ref.is_file():
        return _bad_input(paths, "synth", f"reference voice not found: {ref}")
    t0 = time.time()
    durations = tts.synth_all(paths.out, ref, backend, online=args.online)
    total = round(sum(durations.values()), 1)
    _log(paths, "synth", {"backend": backend, "ref": str(ref) if ref else None,
                          "durations": durations, "total_seconds": total,
                          "seconds_to_synthesize": round(time.time() - t0, 1)})
    _say(f"synth: ok backend={backend} {len(durations)} segments, {total}s of narration "
         f"-> {paths.audio / 'durations.json'}")
    return EXIT_OK


def cmd_record(args) -> int:
    paths = Paths(args.out)
    scen = _load_scenario(args.scenario, paths, check_files=True)
    durations = _read_json(paths.audio / "durations.json", "run synth first")
    m = _mod("drive").record(scen, paths.out, args.capture, args.headless, durations)
    _log(paths, "record", m)
    steps = m.get("steps", [])
    failure = _record_failure(m)
    _say(f"record: {'FAIL' if failure else 'ok'} capture={args.capture} "
         f"{_step_summary(steps)} end={m.get('end_t', 0):.1f}s -> {paths.raw / 'capture.mp4'}"
         + (f" ({failure})" if failure else ""))
    return EXIT_FAIL if failure else EXIT_OK


def cmd_edit(args) -> int:
    paths = Paths(args.out)
    scen = _scenario_for(args, paths)
    final = _mod("edit").build(paths.out, scen)
    _say(f"edit: ok -> {final}")
    return EXIT_OK


def cmd_verify(args) -> int:
    paths = Paths(args.out)
    scen = _scenario_for(args, paths)
    res = _mod("verify").check(paths.out, scen)
    checks = res.get("checks", [])
    failed = [c["name"] for c in checks if not c.get("pass")]
    _say(f"verify: {'PASS' if res.get('pass') else 'FAIL'} {len(checks) - len(failed)}/{len(checks)} "
         f"checks duration={res.get('duration', 0):.1f}s"
         + (f" failed: {', '.join(failed)}" if failed else ""))
    return EXIT_OK if res.get("pass") else EXIT_FAIL


def cmd_run(args) -> int:
    env = _mod("env")
    report = _mod("report")
    paths = Paths(args.out)
    _mod("tts").set_offline_env(args.online)   # before doctor/synth can import chatterbox
    if args.narration == "llm" and not (args.base_url and args.model):
        return _bad_input(paths, "run", "--narration llm needs --base-url and --model "
                                        "(or DEMO_SMOKE_BASE_URL / DEMO_SMOKE_MODEL)")
    scen = _load_scenario(args.scenario, paths, check_files=True)
    dry = markers = ver = None
    env_rep: dict = {}
    source = args.narration
    stage = "doctor"
    timings: dict = {}
    llm_detail: dict | None = None      # attempts / problems / fallback of the llm narration (bench reads it)
    t_run = time.time()

    def done(verdict: str, error: str | None = None) -> None:
        timings["total"] = round(time.time() - t_run, 1)
        _log(paths, "run", {"verdict": verdict, "error": error, "stage": stage,
                            "narration_source": source, "timings": timings,
                            "llm": ({k: llm_detail[k] for k in ("attempts", "problems", "fallback", "fallback_reason")}
                                    if llm_detail else None)})
        rp, jp = report.write(paths.out, scen, dry, markers, ver, env_rep, source, verdict, error)
        _say(f"run: {verdict}" + (f" ({error})" if error else "") + f" -> {rp}, {jp}")

    try:
        t0 = time.time()
        env_rep = env.detect(args.base_url if source == "llm" else None,
                             args.model if source == "llm" else None, timeout=args.timeout)
        _log(paths, "doctor", env_rep)
        timings[stage] = round(time.time() - t0, 1)
        _say(f"[doctor] ffmpeg={'ok' if env_rep['ffmpeg'] else 'MISSING'} "
             f"chrome={'ok' if env_rep['chrome'] else 'MISSING'} torch={env_rep['torch_device']} "
             f"chatterbox={'yes' if env_rep['chatterbox'] else 'no'}")
        missing = [k for k in ("ffmpeg", "chrome") if not env_rep.get(k)]
        if missing:
            raise PipelineError(f"{', '.join(missing)} missing; " + "; ".join(env_rep["hints"]))

        stage = "dryrun"
        t0 = time.time()
        dry = _mod("drive").dryrun(scen, paths.out, headless=args.headless)
        _log(paths, "dryrun", dry)
        timings[stage] = round(time.time() - t0, 1)
        _say(f"[dryrun] {dry['verdict']} {_step_summary(dry.get('steps', []))}")
        if dry["verdict"] != "PASS":
            done("FAIL")
            return EXIT_FAIL

        stage = "narrate"
        t0 = time.time()
        narration = _mod("narration")
        note = ""
        if source == "llm":
            llm_detail = narration.from_llm_detail(scen, args.base_url, args.model, timeout=args.timeout)
            narr, source, note = llm_detail["narration"], llm_detail["source"], llm_detail["note"]
        else:
            narr = narration.template(scen)
        errors = narration.validate(narr, scen)
        if errors:
            raise PipelineError("narration invalid: " + "; ".join(errors))
        _write_narration(paths, narr)
        timings[stage] = round(time.time() - t0, 1)
        _say(f"[narrate] source={source} {len(narr['steps'])} steps" + (f" ({note})" if note else ""))

        stage = "synth"
        t0 = time.time()
        tts = _mod("tts")
        backend = tts.resolve_backend(args.tts)
        ref = Path(args.ref) if args.ref else None
        if ref is not None and not ref.is_file():
            raise PipelineError(f"reference voice not found: {ref}")
        if backend != "tone" and ref is None:
            _say("[synth] note: no --ref given, using the model's default voice")
        durations = tts.synth_all(paths.out, ref, backend, online=args.online)
        timings[stage] = round(time.time() - t0, 1)
        _say(f"[synth] backend={backend} {len(durations)} segments "
             f"{round(sum(durations.values()), 1)}s")

        stage = "record"
        t0 = time.time()
        markers = _mod("drive").record(scen, paths.out, args.capture, args.headless, durations)
        _log(paths, "record", markers)
        timings[stage] = round(time.time() - t0, 1)
        rec_failure = _record_failure(markers)
        _say(f"[record] capture={args.capture} {_step_summary(markers.get('steps', []))} "
             f"end={markers.get('end_t', 0):.1f}s" + (f" ({rec_failure})" if rec_failure else ""))
        if rec_failure:
            done("FAIL", rec_failure)
            return EXIT_FAIL

        stage = "edit"
        t0 = time.time()
        final = _mod("edit").build(paths.out, scen)
        timings[stage] = round(time.time() - t0, 1)
        _say(f"[edit] -> {final}")

        stage = "verify"
        t0 = time.time()
        ver = _mod("verify").check(paths.out, scen)
        timings[stage] = round(time.time() - t0, 1)
        failed = [c["name"] for c in ver.get("checks", []) if not c.get("pass")]
        _say(f"[verify] {'PASS' if ver.get('pass') else 'FAIL'}"
             + (f" failed: {', '.join(failed)}" if failed else ""))

        stage = "report"
        if ver.get("pass"):
            done("PASS")
            return EXIT_OK
        done("FAIL", "verification checks failed: " + ", ".join(failed))
        return EXIT_FAIL
    except KeyboardInterrupt:
        done("ERROR", f"interrupted during {stage}")
        return 130
    except Exception as e:  # noqa: BLE001 - every stage failure becomes exit 3 + report
        msg = f"{stage} failed: {e}"
        _err(msg)
        done("ERROR", msg)
        return EXIT_ERROR


# --------------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="python -m demo_smoke",
                description="Offline demo smoke kit: smoke-test a local app and record a narrated MP4.")
    p.add_argument("--version", action="version", version=f"demo_smoke {__version__}")
    sub = p.add_subparsers(dest="cmd", metavar="<cmd>")
    sub.required = True

    def out_arg(sp, required=False):
        sp.add_argument("--out", default=None if required else DEFAULT_OUT, required=required,
                        help=f"output directory (default: {DEFAULT_OUT})")

    def llm_args(sp, required=False, model_required=None):
        # DEMO_SMOKE_BASE_URL / DEMO_SMOKE_MODEL satisfy a required flag (argparse ignores
        # defaults on required options, so only require when the env var is unset).
        base_default = os.environ.get("DEMO_SMOKE_BASE_URL") or None
        model_default = os.environ.get("DEMO_SMOKE_MODEL") or None
        if model_required is None:
            model_required = required
        sp.add_argument("--base-url", required=required and base_default is None, default=base_default,
                        help="OpenAI-compatible base URL, e.g. http://localhost:11434/v1 "
                             "(env: DEMO_SMOKE_BASE_URL)")
        sp.add_argument("--model", required=model_required and model_default is None, default=model_default,
                        help="model name, e.g. qwen3-coder:30b (env: DEMO_SMOKE_MODEL)")
        sp.add_argument("--timeout", type=int, default=180, help="LLM request timeout (s)")

    def tts_args(sp):
        sp.add_argument("--tts", choices=TTS_CHOICES, default="auto", help="TTS backend")
        sp.add_argument("--ref", default=None,
                        help="reference voice WAV (30-90 s of clean single-speaker speech)")
        sp.add_argument("--online", action="store_true",
                        help="allow HF downloads (default: HF_HUB_OFFLINE=1)")

    sp = sub.add_parser("doctor", help="environment report")
    llm_args(sp)
    out_arg(sp)
    sp.set_defaults(fn=cmd_doctor)

    sp = sub.add_parser("check-model", help="does the model return a tool call? (--list: model ids)")
    llm_args(sp, required=True, model_required=False)   # --model checked in cmd_check_model (--list needs none)
    sp.add_argument("--list", action="store_true",
                    help="print the model ids the server offers (GET /v1/models) and exit")
    out_arg(sp)
    sp.set_defaults(fn=cmd_check_model)

    sp = sub.add_parser("prefetch", help="download Chatterbox weights (online)")
    sp.add_argument("--tts", choices=("auto", "turbo", "nano", "classic"), default="auto",
                    help="which weights to cache; auto = what `run --tts auto` picks on this machine")
    out_arg(sp)
    sp.set_defaults(fn=cmd_prefetch)

    sp = sub.add_parser("voice-check", help="synthesize one test sentence")
    tts_args(sp)
    out_arg(sp)
    sp.set_defaults(fn=cmd_voice_check)

    sp = sub.add_parser("dryrun", help="drive the scenario once, no recording")
    sp.add_argument("scenario", metavar="SCENARIO")
    out_arg(sp)
    sp.add_argument("--headless", action="store_true")
    sp.set_defaults(fn=cmd_dryrun)

    sp = sub.add_parser("narrate-template", help="narration.json from the scenario text")
    sp.add_argument("scenario", metavar="SCENARIO")
    out_arg(sp)
    sp.set_defaults(fn=cmd_narrate_template)

    sp = sub.add_parser("narrate-llm", help="narration.json written by the local model")
    sp.add_argument("scenario", metavar="SCENARIO")
    out_arg(sp)
    llm_args(sp, required=True)
    sp.set_defaults(fn=cmd_narrate_llm)

    sp = sub.add_parser("narrate-validate", help="validate audio/narration.json")
    sp.add_argument("scenario", metavar="SCENARIO", nargs="?", default=None,
                    help="scenario file (default: the one saved by the last command)")
    out_arg(sp)
    sp.add_argument("--max-seconds", type=float, default=None, help="override max_length_seconds")
    sp.set_defaults(fn=cmd_narrate_validate)

    sp = sub.add_parser("synth", help="synthesize every narration segment")
    out_arg(sp)
    tts_args(sp)
    sp.set_defaults(fn=cmd_synth)

    sp = sub.add_parser("record", help="paced run with screen capture")
    sp.add_argument("scenario", metavar="SCENARIO")
    out_arg(sp)
    sp.add_argument("--capture", choices=CAPTURE_CHOICES, default="screencast")
    sp.add_argument("--headless", action="store_true")
    sp.set_defaults(fn=cmd_record)

    sp = sub.add_parser("edit", help="assemble final/<slug>.mp4")
    sp.add_argument("scenario", metavar="SCENARIO", nargs="?", default=None)
    out_arg(sp)
    sp.set_defaults(fn=cmd_edit)

    sp = sub.add_parser("verify", help="check the final video")
    sp.add_argument("scenario", metavar="SCENARIO", nargs="?", default=None)
    out_arg(sp)
    sp.set_defaults(fn=cmd_verify)

    sp = sub.add_parser("run", help="whole pipeline")
    sp.add_argument("scenario", metavar="SCENARIO")
    out_arg(sp)
    tts_args(sp)
    sp.add_argument("--capture", choices=CAPTURE_CHOICES, default="screencast")
    sp.add_argument("--narration", choices=("template", "llm"), default="template")
    sp.add_argument("--headless", action="store_true")
    llm_args(sp)
    sp.set_defaults(fn=cmd_run)

    # Onboarding commands (record-ref, devices, creds, init-scenario, validate, inspect); each one
    # sets its own --out semantics and fn= so the dispatch in main() is unchanged.
    run_map: dict = {}
    onboard_audio.register(sub, run_map)
    onboard_scenario.register(sub, run_map)
    # Bench commands (bench, bench-meta, opencode-events); same register() convention.  ``bench``
    # carries --record-screen / --meta-narrate / --meta-from-clips and calls bench_meta itself.
    bench.register(sub, run_map)
    bench_meta.register(sub, run_map)
    opencode_events.register(sub, run_map)
    return p


# Commands whose --out is not a Paths() output directory (record-ref writes a WAV file).
_NO_PATHS_OUT = ("record-ref",)
# Commands whose --out is a plain directory with only a logs/ subfolder (bench directories hold
# runs/<driver>/r<N>/ and meta/, never raw/ audio/ clips/ final/).
_PLAIN_DIR_OUT = ("bench", "bench-meta")


def _load_dotenv(argv: list[str] | None) -> None:
    """Export ``<kit>/.env`` (and ``--env-file``) into ``os.environ`` for names not already set.

    Runs before the parser is built so ``DEMO_SMOKE_BASE_URL`` / ``DEMO_SMOKE_MODEL`` from
    ``.env`` also satisfy the required flags.  ``op://`` values are not resolved here:
    ``dotenv.load_env`` defers them and ``drive.login`` resolves only the two names a
    scenario's login block uses, when it runs (no vault unlock for doctor/synth/record,
    and the secret is never in the environment Chrome or ffmpeg inherit).
    ``--help`` / ``--version`` skip it (no point unlocking a vault to print usage), and so do
    the ``creds`` subcommands: they take ``--env-file`` and resolve names themselves, so
    ``creds check`` reports the real source (``.env`` / ``op://``) and ``creds set`` never
    runs ``op`` for an unrelated name.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    dotenv.forget_deferred()
    if not argv or any(a in ("-h", "--help", "--version") for a in argv):
        return
    if next((a for a in argv if not a.startswith("-")), None) == "creds":
        return
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--env-file", dest="env_file", default=None)
    try:
        ns, _ = pre.parse_known_args(argv)
    except SystemExit:
        ns = None
    files: list = []
    if ns is not None and ns.env_file:
        files.append(ns.env_file)
    files.append(None)   # the kit's own .env (a missing file is a no-op)
    for f in files:
        try:
            dotenv.load_env(f, resolve_refs=False)
        except Exception as e:  # noqa: BLE001 - a broken .env must not block any command
            _err(f"could not load {dotenv.env_path(f)}: {e}")


def main(argv: list[str] | None = None) -> int:
    _load_dotenv(argv)
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:  # --help / --version / usage errors
        return int(e.code or 0)
    try:
        return int(args.fn(args))
    except ScenarioError as e:
        _log_failure(args, str(e), EXIT_BAD_INPUT)
        _err(str(e))
        return EXIT_BAD_INPUT
    except KeyboardInterrupt:
        _err("interrupted")
        return 130
    except Exception as e:  # expected tooling failures: one line, no traceback
        if os.environ.get("DEMO_SMOKE_DEBUG"):
            raise
        msg = f"{args.cmd}: {e}"
        _log_failure(args, msg, EXIT_ERROR)
        _err(msg)
        return EXIT_ERROR


def _log_failure(args, msg: str, code: int) -> None:
    """``<out>/logs/<cmd>.json`` = {"error", "exit_code"} on exit 3/4 (``run`` writes its own)."""
    out = getattr(args, "out", None)
    if not out or getattr(args, "cmd", "") == "run" or getattr(args, "cmd", "") in _NO_PATHS_OUT:
        return
    try:
        if args.cmd in _PLAIN_DIR_OUT:
            logs = Path(out) / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            (logs / f"{args.cmd}.json").write_text(
                json.dumps({"error": msg, "exit_code": code}, indent=2), encoding="utf-8")
        else:
            _log(Paths(out), args.cmd, {"error": msg, "exit_code": code})
    except OSError:
        pass
