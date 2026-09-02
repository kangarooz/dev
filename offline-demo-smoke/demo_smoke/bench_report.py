"""``DIR/report.md`` + ``DIR/bench.json`` for a bench: one table row per driver (mean over
repeats), baseline rows flagged ``manual``, a "what differed" list, links to every run, and a
per-run appendix.  Plain markdown only (no HTML).

What the numbers mean (and do not): ``tool calls`` counts every tool call OpenCode made (kit
commands *and* file reads/writes); ``kit calls`` in the appendix counts only ``python -m demo_smoke``
commands, and the playbook-minimum sentence compares those.  ``total min`` is wall time of the whole
driver process (start-up, model load and warm-up included), averaged over the PASS runs only - a
timed-out or errored repeat is listed per run in the notes and in the appendix, never folded into
the mean next to ``PASS k/n``.  Every other mean in a row comes from the same PASS runs (a run that
died after two tool calls must not drag a driver's tool-call mean down; over every run only when
none passed), and says ``(k/n)`` when it came from k of the row's n runs.  A template narration
always validates: its PASS says the pipeline works, nothing about narration quality.  ``on-screen
refs`` rewards narration that names what the expectations look for, which the template gets by
construction and a model that copies step titles also gets - it is a sanity signal, not a quality
score, so the "what differed" list only quotes it for model-authored narration.  The template
driver is the pipeline-only baseline: it runs no model, so "fastest"/"slowest" and the
manual-baseline comparison rank model drivers only and quote the template separately.  Token
fields are blank when the events did not carry them; the cost is OpenCode's own catalog estimate
and is blank (never ``$0.0000``) when OpenCode has no price for the model, which is every
``@<base-url>`` override.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

from .bench import PLAYBOOK_MIN_KIT_COMMANDS, STAGES

VERDICTS = ("PASS", "FAIL", "ERROR")
BASELINE_KEYS = ("driver", "model", "date", "verdict", "total_minutes", "notes", "narration_source", "tool_calls")
COLUMNS = ("driver", "model", "verdict", "total min", "narration", "tool calls", "words", "on-screen refs",
           "validation retries", "video s", "tokens / cost", "notes")


# --------------------------------------------------------------------------- helpers


def _cell(v, limit: int = 60) -> str:
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        s = f"{v:.2f}".rstrip("0").rstrip(".") if abs(v) < 100 else f"{v:.0f}"
    else:
        s = str(v)
    s = " ".join(s.split()).replace("|", "\\|")
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _nums(values: list) -> list[float]:
    return [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]


def _mean(values: list) -> float | None:
    nums = _nums(values)
    return round(mean(nums), 3) if nums else None


def _count(values: list) -> int:
    """How many of ``values`` are numbers (the ``k`` of a ``(k/n)`` cell)."""
    return len(_nums(values))


def _minutes(seconds) -> float | None:
    """Seconds -> minutes with one decimal (the resolution every minutes figure in the report uses)."""
    return round(float(seconds) / 60.0, 1) if isinstance(seconds, (int, float)) else None


def driver_label(row: dict) -> str:
    """The driver as shown in the table and the prose: the spec without an ``@<base-url>`` override
    (``opencode:fake/scripted``) and ``llm:<model>`` for the llm driver; manual rows keep their driver."""
    spec = str(row.get("driver") or "-")
    kind = row.get("kind")
    if kind == "opencode":
        return spec.split("@", 1)[0]
    if kind == "llm" and row.get("model"):
        return f"llm:{row['model']}"
    return spec


def _minutes_cell(row: dict) -> str:
    """Bench-measured minutes always with one decimal (``1.0``, not ``1``); manual rows as written."""
    v = row.get("total_minutes")
    if isinstance(v, (int, float)) and not isinstance(v, bool) and not row.get("manual"):
        return f"{float(v):.1f}"
    return _cell(v)


def _verdict_label(verdicts: list[str]) -> str:
    """``PASS`` / ``FAIL`` / ``ERROR`` when every run agrees, else ``PASS k/n``; when the non-passing
    runs are of more than one kind the breakdown follows: ``PASS 0/2 (FAIL 1, ERROR 1)``."""
    if not verdicts:
        return "ERROR"
    if all(v == verdicts[0] for v in verdicts):
        return verdicts[0]
    n_pass = sum(1 for v in verdicts if v == "PASS")
    label = f"PASS {n_pass}/{len(verdicts)}"
    others = [v for v in VERDICTS[1:] if v in verdicts] + sorted({v for v in verdicts if v not in VERDICTS})
    if len(others) > 1:
        label += " (" + ", ".join(f"{v} {verdicts.count(v)}" for v in others) + ")"
    return label


def _tokens_cell(tokens, cost) -> str:
    if tokens is None and cost is None:
        return "-"
    parts = []
    if tokens is not None:
        parts.append(f"{int(tokens):,} tok")
    if cost is not None:
        parts.append(f"${float(cost):.4f}")
    return " / ".join(parts)


def _with_n(cell: str, row: dict, key: str) -> str:
    """Append ``(k/n)`` to a mean's cell when fewer than all of the row's runs reported the value."""
    counts = row.get("counts") if isinstance(row.get("counts"), dict) else {}
    k, n = counts.get(key), row.get("runs")
    if cell == "-" or not isinstance(k, int) or not isinstance(n, int) or k >= n:
        return cell
    return f"{cell} ({k}/{n})"


def _link(label: str, rel: str | None) -> str:
    return f"[{label}]({rel})" if rel else f"{label} (missing)"


def _run_rel(run: dict, out: Path, sub: str | None) -> str | None:
    base = Path(run.get("out") or "")
    try:
        rel = base.relative_to(out)
    except ValueError:
        rel = Path("runs") / str(run.get("slug")) / f"r{run.get('run')}"
    if sub is None:
        return rel.as_posix()
    return (rel / sub).as_posix()


# --------------------------------------------------------------------------- baseline


# optional numeric keys of a baseline entry and the unit the table assumes for them
BASELINE_UNITS = {"total_minutes": "minutes", "tool_calls": "count", "narration_words": "words",
                  "references_on_screen": "fraction 0..1", "validation_retries": "count", "video_seconds": "seconds",
                  "tokens_total": "tokens", "cost": "USD"}


def _check_baseline_number(p: Path, i: int, key: str, value) -> None:
    """``TypeError`` when an optional numeric baseline field is not a number, ``ValueError`` when it is
    out of range for the bench's units."""
    if value is None:
        return
    unit = BASELINE_UNITS[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{p}: entry {i}: {key} must be a number ({unit}), got {value!r}")
    if value < 0:
        raise ValueError(f"{p}: entry {i}: {key} must not be negative ({unit}), got {value!r}")
    if key == "references_on_screen" and value > 1:
        raise ValueError(f"{p}: entry {i}: references_on_screen is a fraction between 0 and 1 (0.8, not 80), "
                         f"got {value!r}")


def load_baseline(path: str | Path) -> list[dict]:
    """The manual entries file: a JSON list of ``{driver, model, date, verdict, total_minutes, ...}``.

    Optional numeric keys are rendered in the table with the bench's own units, so they are
    checked here (:data:`BASELINE_UNITS`): ``total_minutes`` in minutes, ``video_seconds`` in
    seconds, ``references_on_screen`` a fraction 0..1, ``tool_calls`` / ``narration_words`` /
    ``validation_retries`` / ``tokens_total`` counts, ``cost`` in USD; none may be negative.
    Raises ``TypeError`` for a wrong container shape or a non-number, ``ValueError`` for a number
    outside its range.
    """
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("entries"), list):
        data = data["entries"]
    if not isinstance(data, list):
        raise TypeError(f"{p}: expected a JSON list of entries")
    rows: list[dict] = []
    for i, e in enumerate(data):
        if not isinstance(e, dict):
            raise TypeError(f"{p}: entry {i} is not an object")
        for key in BASELINE_UNITS:
            _check_baseline_number(p, i, key, e.get(key))
        rows.append({k: e.get(k) for k in BASELINE_KEYS} | {k: v for k, v in e.items() if k not in BASELINE_KEYS})
    return rows


def baseline_rows(entries: list[dict]) -> list[dict]:
    rows = []
    for e in entries or []:
        verdict = str(e.get("verdict") or "-").upper()
        note = " ".join(x for x in (f"manual entry{(' ' + str(e['date'])) if e.get('date') else ''};",
                                    str(e.get("notes") or "")) if x).strip("; ").strip()
        rows.append({
            "driver": str(e.get("driver") or "manual"), "label": str(e.get("driver") or "manual"),
            "kind": "manual", "slug": None, "manual": True,
            "model": e.get("model"), "verdict": verdict, "verdicts": [verdict], "runs": 1,
            "total_minutes": (float(e["total_minutes"]) if isinstance(e.get("total_minutes"), (int, float)) else None),
            "narration_source": e.get("narration_source"), "tool_calls": e.get("tool_calls"),
            "narration_words": e.get("narration_words"), "references_on_screen": e.get("references_on_screen"),
            "validation_retries": e.get("validation_retries"), "video_seconds": e.get("video_seconds"),
            "tokens_total": e.get("tokens_total"), "cost": e.get("cost"), "date": e.get("date"),
            "notes": note or "manual entry",
        })
    return rows


# --------------------------------------------------------------------------- aggregate


def _row_notes(runs: list[dict]) -> str:
    n = len(runs)
    notes: list[str] = []
    fallbacks = sum(1 for r in runs if r["kind"] != "template"
                    and (r.get("narration") or {}).get("source") == "template")
    if fallbacks and runs[0]["kind"] == "opencode":
        notes.append(f"used narrate-template instead of writing narration.json on {fallbacks}/{n}")
    elif fallbacks:
        notes.append(f"fell back to template narration on {fallbacks}/{n}")
    agent = sum(1 for r in runs if (r.get("narration") or {}).get("source") == "agent")
    if agent and runs[0]["kind"] == "opencode":
        notes.append(f"agent wrote narration.json on {agent}/{n}")
    perms = sum((r.get("opencode") or {}).get("permission_prompts") or 0 for r in runs)
    denied = sum((r.get("opencode") or {}).get("denied") or 0 for r in runs)
    if perms:
        notes.append(f"{perms} permission prompt(s)")
    if denied:
        notes.append(f"{denied} denied call(s)")
    limits = sum(1 for r in runs if (r.get("opencode") or {}).get("step_limit_reached"))
    if limits:
        notes.append(f"step limit hit on {limits}/{n}")
    chained = sum((r.get("opencode") or {}).get("chained_kit_calls") or 0 for r in runs)
    if chained:
        notes.append(f"{chained} bash call(s) chained several kit commands (each counted; stage seconds split evenly)")
    for r in runs:
        if r.get("verdict") != "PASS":
            what = r.get("failing_stage") or "?"
            if r.get("failing_step"):
                what += f"/{r['failing_step']}"
            notes.append(f"r{r.get('run')} {r.get('verdict')} at {what}"
                         + (f": {r['error']}" if r.get("error") else ""))
        elif r.get("error"):
            notes.append(f"r{r.get('run')} PASS with an error reported: {r['error']}")
    if n > 1:
        mins = [_minutes(r.get("wall_s")) for r in runs]
        notes.append("min per run: " + ", ".join(f"r{r.get('run')} {m:.1f}" if m is not None else f"r{r.get('run')} -"
                                                 for r, m in zip(runs, mins)))
    return "; ".join(notes)


def aggregate(runs: list[dict]) -> list[dict]:
    """One row per driver slug: verdict label, means over repeats, per-run verdict list, notes.

    Every mean (minutes, tool calls, words, tokens, stages ...) is taken over the driver's PASS
    runs - the same population as ``total min`` - so an errored repeat never dilutes it; when no
    run passed the means cover every run.  ``counts[key]`` is how many of those runs reported the
    value and ``runs`` the row's total, so the table can mark a mean ``(k/n)``.
    """
    groups: dict[str, list[dict]] = {}
    for r in runs:
        groups.setdefault(str(r.get("slug")), []).append(r)
    rows: list[dict] = []
    for slug, group in groups.items():
        first = group[0]
        narr_all = [r.get("narration") or {} for r in group]
        sources: list[str] = []
        source_counts: dict[str, int] = {}
        for x in narr_all:
            if x.get("source"):
                if x["source"] not in sources:
                    sources.append(x["source"])
                source_counts[x["source"]] = source_counts.get(x["source"], 0) + 1
        passed = [r for r in group if r.get("verdict") == "PASS"]
        pop = passed or group                                   # the population every mean is taken over
        narr = [r.get("narration") or {} for r in pop]
        oc = [r.get("opencode") or {} for r in pop if r.get("opencode")]
        walls_all = [r.get("wall_s") for r in group]
        walls_pass = [r.get("wall_s") for r in passed]
        pass_minutes = _minutes(_mean(walls_pass)) if passed else None
        values = {
            "tool_calls": [x.get("tool_calls") for x in oc], "kit_tool_calls": [x.get("kit_tool_calls") for x in oc],
            "narration_words": [x.get("total_words") for x in narr],
            "references_on_screen": [x.get("references_on_screen") for x in narr],
            "validation_retries": [x.get("retries") for x in narr],
            "validation_errors": [x.get("validation_errors") for x in narr],
            "video_seconds": [(r.get("video") or {}).get("duration") for r in pop],
            "audio_seconds": [(r.get("audio") or {}).get("total_seconds") for r in pop],
            "tokens_total": [x.get("tokens_total") for x in oc], "cost": [x.get("cost") for x in oc],
        }
        rows.append({
            "driver": first.get("driver"), "label": driver_label(first), "kind": first.get("kind"), "slug": slug,
            "manual": False,
            "model": first.get("model"), "verdict": _verdict_label([r.get("verdict") or "ERROR" for r in group]),
            "verdicts": [r.get("verdict") for r in group], "runs": len(group), "passed_runs": len(passed),
            # the table's minutes: mean over the PASS runs (every run only when none passed)
            "total_minutes": pass_minutes if passed else _minutes(_mean(walls_all)),
            "pass_minutes": pass_minutes,
            "all_runs_minutes": _minutes(_mean(walls_all)),
            "run_minutes": [_minutes(w) for w in walls_all],
            "mean_over": "PASS" if passed else "all",             # which runs every mean below covers
            "stages": {s: _mean([(r.get("stages") or {}).get(s) for r in pop]) for s in STAGES},
            "narration_source": "/".join(sources) if sources else None,
            "narration_sources": source_counts,
            **{k: (_mean(v) if (v and (k not in ("tool_calls", "kit_tool_calls", "tokens_total", "cost") or oc))
                   else None) for k, v in values.items()},
            "counts": {k: _count(v) for k, v in values.items()},
            "notes": _row_notes(group),
        })
    return rows


# --------------------------------------------------------------------------- differences


def differences(rows: list[dict], runs: list[dict], baseline: list[dict] | None = None) -> list[str]:
    """Plain sentences about what set the drivers apart (empty when there is one clean row)."""
    out: list[str] = []
    auto = [r for r in rows if not r.get("manual")]
    for r in auto:
        n = r["runs"]
        group = [x for x in runs if x.get("slug") == r["slug"]]
        bad = [x for x in group if x.get("verdict") != "PASS"]
        label = r.get("label") or driver_label(r)
        if bad:
            stages = sorted({str(x.get("failing_stage") or "?") for x in bad})
            out.append(f"{label}: {len(bad)}/{n} run(s) did not pass "
                       f"({', '.join(x.get('verdict') or 'ERROR' for x in bad)}; stage {', '.join(stages)}).")
        if r["kind"] != "template":
            fb = sum(1 for x in group if (x.get("narration") or {}).get("source") == "template")
            if fb and r["kind"] == "opencode":
                out.append(f"{label} ran narrate-template instead of writing narration.json itself on "
                           f"{fb}/{n} run(s) (the playbook's fallback; the model never authored narration).")
            elif fb:
                out.append(f"{label} fell back to template narration on {fb}/{n} run(s) "
                           f"(the model's JSON was rejected twice or the request failed).")
        if r["kind"] == "opencode":
            tc = r.get("tool_calls")
            kit = r.get("kit_tool_calls")
            if kit is not None:
                rel = "below" if kit < PLAYBOOK_MIN_KIT_COMMANDS else "at or above"
                over = "its PASS runs" if r.get("mean_over", "PASS") == "PASS" else "its runs (none passed)"
                out.append(f"{label} ran {kit:g} kit commands per run, {rel} the playbook minimum of "
                           f"{PLAYBOOK_MIN_KIT_COMMANDS} ({tc:g} tool calls per run, including file reads "
                           f"and narration writes; mean over {over}).")
            for x in group:
                if x.get("verdict") == "PASS" and x.get("error"):
                    out.append(f"{label} r{x.get('run')} passed although the session reported an error "
                               f"({x['error']}); the video was delivered and verified, so the run counts as PASS.")
            perms = sum((x.get("opencode") or {}).get("permission_prompts") or 0 for x in group)
            denied = sum((x.get("opencode") or {}).get("denied") or 0 for x in group)
            if perms or denied:
                out.append(f"{label}: {perms} permission prompt(s) and {denied} denied call(s) "
                           f"(both should be 0 under the demo-smoke agent rules).")
            if any((x.get("opencode") or {}).get("step_limit_reached") for x in group):
                out.append(f"{label} hit the agent step limit on at least one run.")
            wrote = sum(1 for x in group if (x.get("narration") or {}).get("source") == "agent")
            if wrote:
                out.append(f"{label} wrote narration.json itself on {wrote}/{n} run(s).")
        if (r.get("validation_retries") or 0) > 0:
            out.append(f"{label} needed {r['validation_retries']:g} narration validation retry(ies) per run.")
    # speed: only runs that delivered a verified video count (an ERROR that died at once is not "fast"),
    # and only model drivers are ranked - the template runs no model, it is the pipeline-only baseline
    timed = [r for r in auto if _pass_minutes(r) is not None]
    model_timed = [r for r in timed if r.get("kind") != "template"]
    template_timed = [r for r in timed if r.get("kind") == "template"]
    if len(model_timed) >= 2:
        fastest = min(model_timed, key=_pass_minutes)
        slowest = max(model_timed, key=_pass_minutes)
        if fastest is not slowest:
            out.append(f"Fastest passing model driver: {driver_label(fastest)} at {_pass_minutes(fastest):.1f} min; "
                       f"slowest passing: {driver_label(slowest)} at {_pass_minutes(slowest):.1f} min "
                       "(mean over each driver's PASS runs; the template driver is left out of this ranking).")
    elif auto and not timed:
        out.append("No driver passed, so there is no fastest driver to name.")
    elif not model_timed and any(r.get("kind") != "template" for r in auto):
        out.append("No model driver passed, so there is no fastest model driver to name.")
    for r in template_timed:
        out.append(f"Pipeline-only baseline: {driver_label(r)} passed in {_pass_minutes(r):.1f} min "
                   "(no model ran, so it is not ranked against the model drivers).")
    # on-screen refs: the template scores by construction, so only model-authored narration is quoted
    refs = [r for r in auto if r.get("references_on_screen") is not None and _model_authored(r)]
    if refs:
        out.append("On-screen references of model-authored narration (a sanity check that the text names what "
                   "is on screen, not a quality score; template rows are left out because the template scores "
                   "by construction): " + "; ".join(f"{driver_label(r)} {r['references_on_screen']:.0%}"
                                                    for r in refs) + ".")
    manual = baseline_rows(baseline or [])
    if manual:
        best = min(model_timed, key=_pass_minutes) if model_timed else None
        pipeline = min(template_timed, key=_pass_minutes) if template_timed else None
        if best is not None:
            here = f"the fastest passing model driver here took {_pass_minutes(best):.1f} min ({driver_label(best)})"
        elif pipeline is not None:
            here = (f"no model driver passed here (the pipeline-only template baseline took "
                    f"{_pass_minutes(pipeline):.1f} min)")
        else:
            here = "no automated driver passed here"
        for entry, m in zip(baseline or [], manual):
            if m.get("total_minutes") is None:
                continue
            notes = str(entry.get("notes") or "").strip()     # the person's own words, not the composed cell
            out.append(f"Manual baseline {m.get('model') or m['driver']} took {m['total_minutes']:.0f} min "
                       f"({m.get('verdict')}) per its own notes" + (f' ("{notes}")' if notes else "")
                       + f"; {here}. The manual figure is what a person wrote down, not a bench "
                       "measurement, and may include setup or a different scenario.")
    return out


def _pass_minutes(row: dict) -> float | None:
    """Mean minutes of a row's PASS runs (``pass_minutes``; older rows: ``total_minutes`` when the label is PASS)."""
    v = row.get("pass_minutes")
    if v is None and row.get("verdict") == "PASS":
        v = row.get("total_minutes")
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _model_authored(row: dict) -> bool:
    """Every run's narration came from a model (``llm``/``agent``), none from the template."""
    if row.get("kind") == "template":
        return False
    sources = row.get("narration_sources") if isinstance(row.get("narration_sources"), dict) else None
    if sources is None:
        sources = {s: 1 for s in str(row.get("narration_source") or "").split("/") if s}
    return bool(sources) and "template" not in sources


# --------------------------------------------------------------------------- markdown


def table(rows: list[dict]) -> list[str]:
    lines = ["| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    for r in rows:
        driver = _cell(r.get("label") or driver_label(r))
        if r.get("manual") and driver.lower() != "manual":
            driver = f"{driver} (manual)"
        refs = r.get("references_on_screen")
        lines.append("| " + " | ".join([
            driver, _cell(r.get("model")), _cell(r.get("verdict")), _minutes_cell(r),
            _cell(r.get("narration_source")), _with_n(_cell(r.get("tool_calls")), r, "tool_calls"),
            _with_n(_cell(r.get("narration_words")), r, "narration_words"),
            _with_n(_cell(f"{refs:.0%}" if isinstance(refs, (int, float)) else None), r, "references_on_screen"),
            _with_n(_cell(r.get("validation_retries")), r, "validation_retries"),
            _with_n(_cell(r.get("video_seconds")), r, "video_seconds"),
            _with_n(_tokens_cell(r.get("tokens_total"), r.get("cost")), r, "tokens_total"),
            _cell(r.get("notes"), 200),
        ]) + " |")
    return lines


def build_markdown(out: Path, bench: dict) -> str:
    out = Path(out)
    runs = bench.get("runs") or []
    rows = list(bench.get("rows") or aggregate(runs)) + baseline_rows(bench.get("baseline") or [])
    name = bench.get("name") or "scenario"
    lines = [f"# Bench: {name}", ""]
    lines += [
        f"- Scenario: `{bench.get('scenario')}`",
        (f"- Drivers: {', '.join(d.get('spec', '?') if isinstance(d, dict) else str(d) for d in bench.get('drivers') or [])}"
         f" x {bench.get('repeat', 1)} run(s) each"),
        (f"- Started {bench.get('started')} - finished {bench.get('finished')} "
         f"({_minutes(bench.get('wall_s')) or 0:.1f} min wall)"),
        f"- TTS: {(bench.get('args') or {}).get('tts')}"
        + (" (headless)" if (bench.get('args') or {}).get('headless') else ""),
    ]
    if bench.get("interrupted"):
        lines.append("- **Interrupted**: the bench was stopped before every run finished.")
    lines += ["", "## Results", ""]
    if rows:
        lines += table(rows)
    else:
        lines.append("_No runs._")
    legend = ("Mean over repeats per driver, minutes to one decimal; `PASS k/n` means only k of n runs passed "
              "(with the FAIL/ERROR breakdown when both occurred). `total min` is the wall time of the whole "
              "driver process - start-up, model load and warm-up included - averaged over that driver's PASS "
              "runs only (over every run when none passed; with `--repeat` the notes list each run's minutes, "
              "the appendix every run). Every other mean in a row covers the same PASS runs (every run only "
              "when none passed), so a repeat that died early never dilutes it; `(k/n)` says the mean came "
              "from k of the row's n runs. A run the bench killed on its timeout is ERROR even if it had "
              "delivered a video. Rows whose driver is `manual` (or marked `(manual)`) come from the baseline "
              "file: figures a person wrote down, not measured by the bench. `driver` shows the spec without "
              "any `@base-url` override (the exact specs are listed above; run directories use the slug shown "
              "under Runs). `tool calls` counts every OpenCode tool call (kit commands and file reads/writes; "
              "the appendix's `kit calls` counts `python -m demo_smoke` commands only, every command of a "
              "chained bash call included); `narration` is where "
              "the text came from (`template` = the scenario's own sentences, so its PASS says nothing about "
              "narration quality; `agent` = the model wrote narration.json). `on-screen refs` = share of "
              "narration segments that share a word with an expectation string or step title - the template "
              "scores this by construction and so does a model that copies step titles, so it is a sanity "
              "signal, not a quality score. `tokens / cost`: the cost is OpenCode's own catalog estimate, "
              "not a provider figure, and is left blank when OpenCode has no price for the model (every "
              "`@base-url` override). The template driver runs no model: it is quoted as the pipeline-only "
              "baseline and left out of the fastest/slowest ranking.")
    lines += ["", legend, ""]
    lines += ["## What differed", ""]
    diffs = bench.get("differences") or differences(rows, runs, bench.get("baseline"))
    lines += [f"- {d}" for d in diffs] or ["- Nothing notable: every driver behaved the same."]
    lines += ["", "## Runs", ""]
    for r in runs:
        video = (r.get("video") or {}).get("path")
        bits = [
            _link("bench.json", _run_rel(r, out, "bench.json")),
            _link("report.md", _run_rel(r, out, "report.md")) if r.get("report") else
            _link("logs/", _run_rel(r, out, "logs") + "/"),
            _link("video", _run_rel(r, out, video)) if video else "no video",
        ]
        if r.get("kind") == "opencode":
            bits.append(_link("events", _run_rel(r, out, "logs/opencode-events.json")))
        if r.get("screen_recording"):
            bits.append(_link("screen recording", r["screen_recording"]))
        mins = _minutes(r.get("wall_s"))
        lines.append(f"- **{r.get('slug')} r{r.get('run')}** - {r.get('verdict')}"
                     + (f", {mins:.1f} min" if mins is not None else "")
                     + f", narration {(r.get('narration') or {}).get('source') or '-'}: " + " · ".join(bits)
                     + (f" - error: {_cell(r.get('error'), 200)}" if r.get("error") else ""))
    if bench.get("meta_video"):
        mv = Path(bench["meta_video"])
        try:
            rel = mv.relative_to(out).as_posix()
        except ValueError:
            rel = mv.as_posix()
        lines += ["", f"Meta recording: [{mv.name}]({rel})"]
    lines += ["", "## Appendix: per-run rows", ""]
    head = ["driver slug", "run", "verdict", "min", "narration", "tool calls", "kit calls", "words", "on-screen refs",
            "retries", "audio s", "video s", "tokens / cost", *STAGES, "error"]
    lines += ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in runs:
        narr = r.get("narration") or {}
        oc = r.get("opencode") or {}
        refs = narr.get("references_on_screen")
        st = r.get("stages") or {}
        lines.append("| " + " | ".join([
            _cell(r.get("slug")), str(r.get("run")), _cell(r.get("verdict")), _cell(_minutes(r.get("wall_s"))),
            _cell(narr.get("source")), _cell(oc.get("tool_calls") if oc else None),
            _cell(oc.get("kit_tool_calls") if oc else None), _cell(narr.get("total_words")),
            _cell(f"{refs:.0%}" if isinstance(refs, (int, float)) else None), _cell(narr.get("retries")),
            _cell((r.get("audio") or {}).get("total_seconds")), _cell((r.get("video") or {}).get("duration")),
            _tokens_cell(oc.get("tokens_total") if oc else None, oc.get("cost") if oc else None),
            *[_cell(st.get(s)) for s in STAGES], _cell(r.get("error"), 120),
        ]) + " |")
    lines += ["", f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} by demo_smoke bench.", ""]
    return "\n".join(lines)


def write(out: Path, bench: dict) -> tuple[Path, Path]:
    """Write ``DIR/report.md`` and ``DIR/bench.json``; return both paths."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    bench.setdefault("rows", aggregate(bench.get("runs") or []))
    bench.setdefault("differences", differences(bench["rows"], bench.get("runs") or [], bench.get("baseline")))
    bench["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    md = build_markdown(out, bench)
    report = out / "report.md"
    report.write_text(md, encoding="utf-8")
    js = out / "bench.json"
    js.write_text(json.dumps(bench, indent=2, default=str), encoding="utf-8")
    return report, js


# --------------------------------------------------------------------------- validation


_RUN_KEYS = ("driver", "kind", "model", "slug", "run", "out", "started", "finished", "wall_s", "stages",
             "verdict", "exit_code", "failing_stage", "failing_step", "error", "narration", "audio", "video",
             "opencode", "llm", "env")
_NARR_KEYS = ("source", "validation_errors", "retries", "words_per_segment", "total_words",
              "estimated_seconds", "references_on_screen")
_OC_KEYS = ("tool_calls", "commands", "assistant_messages", "permission_prompts", "denied", "steps",
            "tokens_in", "tokens_out", "cost", "narration_written_by_agent", "used_narrate_template")


def validate(bench: dict) -> list[str]:
    """Problems with a ``bench.json`` dict (top level and every run); ``[]`` when it is well-formed."""
    errs: list[str] = []
    if not isinstance(bench, dict):
        return ["bench.json must be an object"]
    for key in ("scenario", "started", "finished", "drivers", "repeat", "runs", "rows", "differences", "baseline"):
        if key not in bench:
            errs.append(f"missing top-level key {key!r}")
    runs = bench.get("runs")
    if not isinstance(runs, list):
        errs.append("runs must be a list")
        runs = []
    for i, r in enumerate(runs):
        if not isinstance(r, dict):
            errs.append(f"runs[{i}] is not an object")
            continue
        for key in _RUN_KEYS:
            if key not in r:
                errs.append(f"runs[{i}] missing {key!r}")
        if r.get("verdict") not in VERDICTS:
            errs.append(f"runs[{i}] verdict {r.get('verdict')!r} not in {VERDICTS}")
        if r.get("kind") not in ("template", "llm", "opencode"):
            errs.append(f"runs[{i}] kind {r.get('kind')!r} unknown")
        if not isinstance(r.get("wall_s"), (int, float)):
            errs.append(f"runs[{i}] wall_s must be a number")
        st = r.get("stages")
        if not isinstance(st, dict) or any(s not in st for s in STAGES):
            errs.append(f"runs[{i}] stages must have {STAGES}")
        narr = r.get("narration")
        if not isinstance(narr, dict):
            errs.append(f"runs[{i}] narration must be an object")
        else:
            for key in _NARR_KEYS:
                if key not in narr:
                    errs.append(f"runs[{i}] narration missing {key!r}")
            ref = narr.get("references_on_screen")
            if ref is not None and not (isinstance(ref, (int, float)) and 0.0 <= ref <= 1.0):
                errs.append(f"runs[{i}] references_on_screen must be in [0, 1]")
        if r.get("kind") == "opencode":
            oc = r.get("opencode")
            if not isinstance(oc, dict):
                errs.append(f"runs[{i}] opencode block missing")
            else:
                for key in _OC_KEYS:
                    if key not in oc:
                        errs.append(f"runs[{i}] opencode missing {key!r}")
        elif r.get("opencode") is not None:
            errs.append(f"runs[{i}] opencode block must be null for a {r.get('kind')} driver")
        if r.get("kind") == "llm" and not isinstance(r.get("llm"), dict):
            errs.append(f"runs[{i}] llm block missing")
    rows = bench.get("rows")
    if isinstance(rows, list):
        slugs = {r.get("slug") for r in runs if isinstance(r, dict)}
        for i, row in enumerate(rows):
            if not isinstance(row, dict) or "verdict" not in row or "driver" not in row:
                errs.append(f"rows[{i}] must have driver and verdict")
            elif row.get("slug") not in slugs and not row.get("manual"):
                errs.append(f"rows[{i}] slug {row.get('slug')!r} has no runs")
    elif "rows" in bench:
        errs.append("rows must be a list")
    if "baseline" in bench and not isinstance(bench.get("baseline"), list):
        errs.append("baseline must be a list")
    return errs
