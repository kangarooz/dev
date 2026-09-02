# Rules for the local model (loaded by OpenCode)

## What this is
The Offline Demo Smoke Kit smoke-tests a feature of a locally running web app
and records a narrated walkthrough as an MP4, fully offline. Everything is
deterministic Python (`python -m demo_smoke <cmd>`). Your job is small:
run the kit's commands in order, write `audio/narration.json`, and report.
The `demo-smoke` agent (`.opencode/agents/demo-smoke.md`) has the full playbook,
with the exact commands and their order; follow it, this file only repeats the rules.

## Interpreter
Run `ls .venv/bin/python` (Windows: `dir .venv\Scripts\python.exe`) once. If it prints the path,
use that path instead of `python` in every command; if it prints `No such file`, use `python`.

## Commands
One-shot with template narration: `python -m demo_smoke run <scenario> --out <out> --narration template`
(add `--ref REF.wav` only when the user gave a clip, `--tts tone` when doctor reports no chatterbox or `tts_ready` false).
Slash commands: `/setup`, `/smoke <scenario> [out] [headless] [ref.wav]`, `/narrate <scenario> <out>` (both required), `/voice-check <ref.wav>`.
No display / unattended: add `--headless` to the dryrun and record commands.
Credentials (`DEMO_USER`, `DEMO_PASS`) come from the environment OpenCode was started in; never put them on the command line.

## Exit codes
0 ok - 2 feature failed (summary line says FAIL) - 3 tool/pipeline error (a line starting with `error:`) - 4 bad input.
On 2 or 3: stop, read `<out>/logs/<cmd>.json`, report. On 3 it holds only `error` and `exit_code`; `report.md`/`result.json` exist only after `run`. Never retry blindly.
Exit 4 from `narrate-validate` (line starts with `narrate-validate: INVALID`; its log has `errors` and `budget`): fix narration.json once, validate again, then fall back to narrate-template. Exit 4 from anything else: stop and report the `error:` line (the log holds only `error` and `exit_code`); do not edit the scenario.
A command cut off by the tool timeout (no summary line, no `error:` line): do not rerun it; report `ERROR (stage: <cmd>, timed out)`.

## Never
- Never write code or edit any file other than `<out>/audio/narration.json` (a scenario under `scenarios/` only when the user explicitly asked for it).
- Never install packages, never fetch the web (`prefetch` and `--online` are denied), never `git push`, never delete files.
- Never run more than one command per step; never run a command that is not in the playbook.
- Never invent results. Read the JSON the command wrote (with the read tool, exact path; do not search).

## Where things are
- Scenarios: `scenarios/*.json` (schema: `scenarios/schema.json`, format: `ARCHITECTURE.md`).
- Outputs: always pass `--out demo-output/<slug>` (the CLI's own default is plain `demo-output/`):
  `logs/<cmd>.json`, `logs/step-NN-<id>.png`, `logs/smoke-results.md`,
  `audio/narration.json`, `audio/seg-*.wav`, `raw/capture.mp4`,
  `final/<slug>.mp4`, `final/thumb-*.png`; `report.md` and `result.json` only after `run`.
- Kit source (read only): `demo_smoke/`. Human docs: `README.md`.
