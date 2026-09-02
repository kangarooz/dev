# Rules for the local model (loaded by OpenCode)

## What this is
The Offline Demo Smoke Kit smoke-tests a feature of a locally running web app
and records a narrated walkthrough as an MP4, fully offline. Everything is
deterministic Python (`python -m demo_smoke <cmd>`). Your job is small:
run the kit's commands in order, write `audio/narration.json`, and report.
The `demo-smoke` agent (`.opencode/agents/demo-smoke.md`) has the full playbook.

## Golden path
0. If `.venv/bin/python` (or `.venv\Scripts\python.exe`) exists, use it instead of `python` everywhere.
1. `python -m demo_smoke doctor --out <out>` (read `<out>/logs/doctor.json`; `tts_ready` false -> use `--tts tone` in step 4)
2. `python -m demo_smoke dryrun <scenario> --out <out>`  (exit 2 = feature FAIL, stop and report)
3. Write `<out>/audio/narration.json` with the write tool, then `python -m demo_smoke narrate-validate <scenario> --out <out>`
4. `python -m demo_smoke synth --out <out> --tts auto [--ref REF.wav]`
5. `python -m demo_smoke record <scenario> --out <out> --capture screencast`
6. `python -m demo_smoke edit --out <out>` then `python -m demo_smoke verify --out <out>`
7. Report: verdict, per-step status, video path, failed checks, next action.

One-shot with template narration: `python -m demo_smoke run <scenario> --out <out> --narration template`.
Slash commands: `/setup`, `/smoke <scenario> [out]`, `/narrate <scenario> <out>` (both required), `/voice-check <ref.wav>`.
No display / unattended: add `--headless` to dryrun and record.

## Exit codes
0 ok - 2 feature failed (summary line says FAIL) - 3 tool/pipeline error (a line starting with `error:`) - 4 bad input.
On 2 or 3: stop, read `<out>/logs/<cmd>.json` (on 3 it holds only `error` and `exit_code`; `report.md`/`result.json` exist only after `run`), report. Never retry blindly.
Exit 4 from `narrate-validate`: fix narration.json once, validate again, then fall back to narrate-template. Exit 4 from anything else: stop and report the `error:` line; do not edit the scenario.

## Never
- Never write code or edit any file other than `<out>/audio/narration.json` (a scenario under `scenarios/` only when the user explicitly asked for it).
- Never install packages, never fetch the web, never `git push`, never delete files.
- Never run more than one command per step; never run a command that is not in the playbook.
- Never invent results. Read the JSON the command wrote (with the read tool, exact path; do not search).

## Where things are
- Scenarios: `scenarios/*.json` (schema: `scenarios/schema.json`, format: `ARCHITECTURE.md`).
- Outputs: always pass `--out demo-output/<slug>` (the CLI's own default is plain `demo-output/`):
  `logs/<cmd>.json`, `logs/step-NN-<id>.png`, `logs/smoke-results.md`,
  `audio/narration.json`, `audio/seg-*.wav`, `raw/capture.mp4`,
  `final/<slug>.mp4`, `final/thumb-*.png`; `report.md` and `result.json` only after `run`.
- Kit source (read only): `demo_smoke/`. Human docs: `README.md`.
