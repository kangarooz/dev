"""``report.md`` + ``result.json`` for a pipeline run."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _cell(v, limit: int = 80) -> str:
    s = "" if v is None else str(v)
    s = " ".join(s.split()).replace("|", "\\|")
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _rel(p: Path, out: Path) -> str:
    try:
        return p.relative_to(out).as_posix()
    except ValueError:
        return str(p)


def _artifacts(out: Path) -> list[str]:
    found: list[str] = []
    for pattern in ("final/*.mp4", "final/thumb-*.png", "report.md", "result.json",
                    "raw/capture.mp4", "audio/narration.json", "audio/durations.json",
                    "audio/seg-*.wav", "audio/voice_check.wav", "logs/markers.json",
                    "logs/smoke-results.md", "logs/step-*.png", "logs/*.json"):
        for p in sorted(out.glob(pattern)):
            rel = _rel(p, out)
            if rel not in found:
                found.append(rel)
    for always in ("report.md", "result.json"):   # written by write() right after
        if always not in found:
            found.append(always)
    return found


def _env_rows(env: dict) -> list[tuple[str, str]]:
    env = env or {}
    rows = [
        ("OS", env.get("os")),
        ("Python", env.get("python")),
        ("ffmpeg", f"{env.get('ffmpeg')} ({env.get('ffmpeg_version')})" if env.get("ffmpeg")
         else "missing"),
        ("ffprobe", env.get("ffprobe") or "missing (ffmpeg -i fallback)"),
        ("Chrome", env.get("chrome") or "missing"),
        ("torch device", env.get("torch_device")),
        ("chatterbox", "yes" if env.get("chatterbox") else "no"),
    ]
    llm = env.get("llm")
    if llm:
        tc = llm.get("tool_call") or {}
        rows.append(("LLM", f"{llm.get('base_url')} model={llm.get('model')} "
                            f"reachable={llm.get('reachable')}"
                            + (f" tool_call={'PASS' if tc.get('pass') else 'FAIL'}" if tc else "")))
    return [(k, _cell(v, 160)) for k, v in rows]


def build_markdown(out: Path, scenario: dict, dryrun: dict | None, markers: dict | None,
                   verify: dict | None, env: dict, narration_source: str, verdict: str,
                   error: str | None = None) -> str:
    name = scenario.get("name", "demo")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [f"# Demo smoke report: {name}", "", f"**Verdict: {verdict}**", ""]
    if error:
        lines += ["## Error", "", f"`{_cell(error, 500)}`", ""]
    lines += [
        f"- Scenario: `{scenario.get('_path', scenario.get('slug', ''))}`",
        f"- App URL: {scenario.get('app_url', '')}",
        f"- Narration source: {narration_source}",
        f"- Output: `{out}`",
        f"- Generated: {now}",
        "",
    ]
    lines += ["## Steps", ""]
    steps = (dryrun or {}).get("steps") or []
    if steps:
        lines += ["| # | id | title | status | seconds | expected | observed | error |",
                  "|---|---|---|---|---|---|---|---|"]
        for i, st in enumerate(steps, 1):
            secs = st.get("seconds")
            lines.append(
                f"| {i} | {_cell(st.get('id'))} | {_cell(st.get('title'))} | "
                f"{_cell(st.get('status'))} | {secs if secs is None else f'{float(secs):.1f}'} | "
                f"{_cell(st.get('expected'))} | {_cell(st.get('observed'))} | "
                f"{_cell(st.get('error'))} |"
            )
        lines.append("")
        if dryrun.get("attempts"):
            lines.append(f"Dry run attempts: {dryrun['attempts']}")
            lines.append("")
        cons = dryrun.get("console_errors") or []
        if cons:
            lines += ["### Console errors", ""] + [f"- `{_cell(c, 200)}`" for c in cons[:20]] + [""]
        fails = dryrun.get("failed_requests") or []
        if fails:
            lines += ["### Failed requests", ""]
            for r in fails[:20]:
                lines.append(f"- {r.get('status')} `{_cell(r.get('url'), 120)}` "
                             f"{_cell(r.get('body_excerpt'), 120)}")
            lines.append("")
    else:
        lines += ["_No dry run results (the pipeline stopped before the dry run)._", ""]
    if markers and markers.get("steps"):
        lines += ["## Recording timeline", "",
                  "| id | start (s) | end (s) | status | wait windows |", "|---|---|---|---|---|"]
        for st in markers["steps"]:
            ww = ", ".join(f"{a:.1f}-{b:.1f}" for a, b in st.get("wait_windows", []))
            lines.append(f"| {_cell(st.get('id'))} | {st.get('t_start', 0):.1f} | "
                         f"{st.get('t_end', 0):.1f} | {_cell(st.get('status'))} | {ww} |")
        lines.append(f"\nOutro at {markers.get('outro_t', 0):.1f} s, capture end at "
                     f"{markers.get('end_t', 0):.1f} s.\n")
    lines += ["## Checks", ""]
    checks = (verify or {}).get("checks") or []
    if checks:
        lines += ["| check | result | detail |", "|---|---|---|"]
        for c in checks:
            lines.append(f"| {_cell(c.get('name'))} | {'PASS' if c.get('pass') else 'FAIL'} | "
                         f"{_cell(c.get('detail'), 160)} |")
        lines.append("")
        if verify.get("duration") is not None:
            lines.append(f"Final video duration: {float(verify['duration']):.1f} s")
            lines.append("")
    else:
        lines += ["_No verification results._", ""]
    lines += ["## Artifacts", ""]
    arts = _artifacts(out)
    lines += [f"- `{a}`" for a in arts] or ["_none_"]
    lines += ["", "## Environment", "", "| item | value |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in _env_rows(env)]
    hints = (env or {}).get("hints") or []
    if hints:
        lines += ["", "Hints:", ""] + [f"- {_cell(h, 200)}" for h in hints]
    lines.append("")
    return "\n".join(lines)


def write(out: Path, scenario: dict, dryrun: dict | None, markers: dict | None,
          verify: dict | None, env: dict, narration_source: str, verdict: str,
          error: str | None = None) -> tuple[Path, Path]:
    """Write ``<out>/report.md`` and ``<out>/result.json``; return both paths."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    md = build_markdown(out, scenario, dryrun, markers, verify, env, narration_source,
                        verdict, error)
    report_path = out / "report.md"
    report_path.write_text(md, encoding="utf-8")
    scen = {k: v for k, v in scenario.items() if not k.startswith("_")}
    scen["_path"] = scenario.get("_path")
    result = {
        "verdict": verdict,
        "error": error,
        "scenario": scen,
        "narration_source": narration_source,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "out": str(out),
        "dryrun": dryrun,
        "markers": markers,
        "verify": verify,
        "env": env,
        "artifacts": _artifacts(out),
        "final_video": next((a for a in _artifacts(out) if a.startswith("final/") and a.endswith(".mp4")), None),
    }
    result_path = out / "result.json"
    result_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return report_path, result_path
