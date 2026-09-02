# Tablet bench runbook: the smoke test of the smoke test

Purpose: on this Windows machine, run the kit under three drivers (no LLM, the local
LM Studio model through OpenCode, a hosted model through OpenCode), record the whole
thing, narrate the numbers in the user's cloned voice, and print the comparison
table. Do NOT edit kit source and do NOT `git push`. You MAY create
`voices/*.wav`, `bench/baseline.json`, and files under `scenarios/` and
`demo-output/`. Read `README.md` (sections "The OpenCode path" and "Benchmark")
if anything is unclear. Commands are PowerShell from `offline-demo-smoke`.

Some steps need the human at the keyboard (speaking into the mic, starting LM
Studio, answering questions). Ask, wait, then continue. Keep one consolidated
report at the end; print it in this session.

## 0. Baseline

```powershell
git checkout main; git pull --ff-only
.venv\Scripts\python.exe -m demo_smoke doctor --out demo-output\doctor
```

Require `ffmpeg=ok chrome=ok chatterbox=yes tts_ready=yes`. Note `disk_free`; stop
and tell the user if it is under 8 GB (the bench writes several videos).

## 1. Local model (LM Studio)

Ask the user to start LM Studio's server (Developer tab, port 1234, a model loaded,
context 32k or more). Then:

```powershell
.venv\Scripts\python.exe -m demo_smoke check-model --base-url http://127.0.0.1:1234/v1 --list
.venv\Scripts\python.exe -m demo_smoke check-model --base-url http://127.0.0.1:1234/v1 --model <id>
```

Record the id and the tool-calling PASS/FAIL. FAIL means that model cannot drive
OpenCode; ask the user to load a different one (Qwen3-Coder 30B-A3B or Qwen3 14B
are known good) and re-check. Use the driver spec
`opencode:lmstudio/<id>@http://127.0.0.1:1234/v1` below, which registers the id
for that run without editing `opencode.json`.

## 2. Hosted model

```powershell
opencode models
```

Pick one hosted id the user is already authenticated for (an `anthropic/...`,
`openai/...`, or `github-copilot/...` line). If none is listed, skip the hosted
driver and say so in the report.

## 3. Reference voice clip

Tell the user a 60 s recording is about to start and to read the passage aloud.
Then:

```powershell
.venv\Scripts\python.exe -m demo_smoke devices
.venv\Scripts\python.exe -m demo_smoke record-ref --out voices\nick.wav --seconds 60
.venv\Scripts\python.exe -m demo_smoke voice-check --ref voices\nick.wav --out demo-output\voice-check --tts auto
```

If `record-ref` warns (noisy, short, clipped) record once more. `voice-check`
loads Turbo on CPU (about 20 s) and writes `demo-output\voice-check\audio\voice_check.wav`;
ask the user to listen to it and confirm it sounds like them before continuing.

## 4. Scenario

Ask the user whether the Centurion / Legion app is reachable right now and, if so,
its URL, the login type, and the feature to demo (Chat with Manuals: upload a few
PDFs, ask a question, see a cited answer). Build the scenario with the kit's own
tools rather than by hand:

```powershell
.venv\Scripts\python.exe -m demo_smoke init-scenario --name "<feature>" --url <url> --out scenarios\<slug>.json --step "Open the app :: ..." --step "Upload manuals :: ..." --step "Ask a question :: ..." --step "Check the citation :: ..."
.venv\Scripts\python.exe -m demo_smoke inspect <url> --out demo-output\inspect
```

Fill each step's `actions` and `expect` from the `inspect` output (see
`ARCHITECTURE.md` for the action and expect shapes; credentials are env var
NAMES only, values via `python -m demo_smoke creds set NAME` which the user runs
in their own shell). Then:

```powershell
.venv\Scripts\python.exe -m demo_smoke validate scenarios\<slug>.json --out demo-output\<slug>
.venv\Scripts\python.exe -m demo_smoke dryrun scenarios\<slug>.json --out demo-output\<slug>
```

If the app is not reachable, use the bundled mock instead so the bench still runs:
start `.venv\Scripts\python.exe -m http.server 8765 --bind 127.0.0.1` from
`tests\fixtures\app` in the background and use
`tests\fixtures\scenarios\fixture-pass.json`. Say clearly in the report which one
was used.

## 5. Baseline rows

Copy `bench\baseline.example.json` to `bench\baseline.json` and ask the user for
the numbers of last week's manual Codex run (rough total minutes, verdict, the Loom
link). Keep the example row's shape; unknown numbers stay `null`.

## 6. Bench

Tell the user the screen will be recorded for the whole run (every window on the
display), so close anything private first. Then, with `<id>` and `<hosted>` from
steps 1 and 2 and `<scenario>` from step 4:

```powershell
.venv\Scripts\python.exe -m demo_smoke bench <scenario> --out demo-output\bench --driver template --driver "opencode:lmstudio/<id>@http://127.0.0.1:1234/v1" --driver opencode:<hosted> --tts auto --ref voices\nick.wav --record-screen --meta-narrate --baseline bench\baseline.json --timeout-s 3600
```

This takes a while (three full runs plus CPU voice synthesis). Run it in the
background and poll. If the hosted driver was skipped, drop that `--driver`.

## 7. Report (print all of this in the session)

- `demo-output\bench\report.md` in full (the comparison table and the differences
  section).
- Paths and sizes of `demo-output\bench\meta\*-bench.mp4` and each
  `demo-output\bench\runs\<driver>\r1\final\*.mp4`.
- Which scenario was used (real app or mock), the LM Studio id and its tool-call
  result, the hosted id, and the voice-check stats.
- Anything that failed, with the last 40 lines of its output.
