# Offline Demo Smoke Kit

Smoke-test a feature of a locally running web app, record a narrated
walkthrough in a cloned voice, and get an MP4 you can hand to anyone. Runs
fully offline once prepared. No subscriptions, no cloud APIs.

What actually happens (`python -m demo_smoke run scenario.json`):

1. **doctor** - checks ffmpeg, Chrome, torch device, Chatterbox, optional LLM endpoint.
2. **dryrun** - drives the scenario in Chrome (clicks, uploads, typing), checks every
   `expect`, writes per-step screenshots and `logs/smoke-results.md`. Exit 2 = the feature is broken.
3. **narrate** - `audio/narration.json` from the scenario text (`template`), a local
   model (`llm`), or written by the OpenCode agent.
4. **synth** - Chatterbox voice cloning from your reference clip, one WAV per segment.
5. **record** - runs the scenario again, paced so actions land while the matching sentence is spoken.
6. **edit** - ffmpeg: places the narration, speeds up long waits, loudness-normalizes, muxes H.264/AAC.
7. **verify** - duration, A/V sync, no black frames, audible narration, thumbnails.
8. **report** - `report.md` + `result.json`.

The only thing an LLM ever does is (a) run these commands and (b) write
narration text. Everything else is deterministic Python.

## Requirements

| Need | Notes |
|---|---|
| Python **3.11** (3.10-3.13 work) | 3.11 recommended: `chatterbox-tts` pins `torch==2.6.0`, which has wheels for 3.9-3.13 only (no 3.14) and none for Intel Macs; 3.11 is what Resemble tests on. |
| Google Chrome (or Chromium) | Driven over CDP. Not bundled. Point `DEMO_SMOKE_CHROME` at the binary if it is somewhere unusual. |
| ffmpeg | Optional. `imageio-ffmpeg` bundles a static build (libx264, aac, loudnorm). A system `ffmpeg` on PATH is used first if present; 4.2 or newer is enough. |
| A local LLM server | Only for the OpenCode agent path or `--narration llm`: Ollama (default), llama.cpp `llama-server`, or LM Studio. Not needed for the no-LLM path. |
| OpenCode | Only for the agent path (`opencode run ...`, `/smoke`). Install it while online (see below); `doctor` prints `opencode=ok|MISSING`. |
| A reference clip | 30-90 s WAV of clean, single-speaker speech, no music, speech starting within the first second. Chatterbox conditions on roughly the first 10 s, so the opening must be good. Only for voice cloning; `--tts tone` needs nothing. Keep it inside the kit directory, in `voices/` where `record-ref` and `/clone-voice` write theirs (e.g. `voices/mine.wav`; `*.wav` is gitignored), so the OpenCode agent can read it without a permission prompt. |
| Disk / GPU | Chatterbox Turbo weights ~2 GB; Nano ~1 GB. GPU optional (see TTS choice). Intel Macs: no torch 2.6.0 wheel, so voice cloning needs Apple Silicon, Linux or Windows (`--tts tone` still works). |

## Online preparation (do this once, with internet)

macOS / Linux:

```bash
git clone <this repo> && cd offline-demo-smoke
bash scripts/setup.sh --tts --torch auto --model qwen3-coder:30b
```

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Tts -Torch auto -Model qwen3-coder:30b
```

What the script does, and the manual equivalent (bash, then PowerShell):

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt                              # playwright, numpy, soundfile, imageio-ffmpeg
# voice cloning (optional; pick the torch index for your accelerator, see "TTS model choice")
pip install torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu126
pip install -r requirements-tts.txt                          # chatterbox-tts
python -m demo_smoke prefetch --tts auto                     # caches what --tts auto will use here: turbo on CUDA/ROCm/MPS, nano on CPU (turbo if the build has no nano)
ollama pull qwen3-coder:30b                                  # only for the agent / --narration llm path
curl -fsSL https://opencode.ai/install | bash                # only for the agent path (or: npm i -g opencode-ai); the scripts do not install it
opencode --version                                           # once, while online: confirms the binary works
python -m demo_smoke doctor --base-url http://localhost:11434/v1 --model qwen3-coder:30b
```

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned          # once; otherwise .venv\Scripts\activate is refused (or use .venv\Scripts\python.exe instead of activating)
py -3.11 -m venv .venv; .venv\Scripts\activate
pip install -r requirements.txt
pip install torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu126   # or .../whl/cpu
pip install -r requirements-tts.txt
python -m demo_smoke prefetch --tts auto
ollama pull qwen3-coder:30b
npm i -g opencode-ai                                         # only for the agent path (see https://opencode.ai for other installers)
opencode --version
python -m demo_smoke doctor --base-url http://localhost:11434/v1 --model qwen3-coder:30b
```

The setup scripts take more options than shown above: `--python PATH`, `--base-url URL`
(for the doctor probe), `--no-doctor`, `--torch-index URL` (PowerShell: `-Python`, `-BaseUrl`,
`-NoDoctor`, `-TorchIndex`, `-TorchVersion`); `bash scripts/setup.sh --help` /
`Get-Help scripts\setup.ps1 -Detailed` list them.

OpenCode's grep, glob and `@` file-picker tools need ripgrep: install it (`apt` / `brew` /
`winget install ripgrep`) or run one search in the OpenCode TUI once while online, which
downloads `rg` into `~/.cache/opencode/bin`. The kit's playbooks never search, so this only
matters when you use those tools yourself offline.

`prefetch` is the step people forget: Chatterbox downloads its weights on first
use, and the kit runs with `HF_HUB_OFFLINE=1` by default, so an un-prefetched
machine fails offline with a clear message. With `--tts` the setup scripts run
`prefetch --tts auto` for you (`--prefetch turbo|nano|classic` to pick, `--prefetch none`
to skip; PowerShell `-Prefetch`). Run it once per TTS model you plan to use; `doctor`
prints `tts_auto=<backend> tts_ready=yes|NO` so you can see before unplugging.

For tool calls to work with Ollama, give the model enough context. Ollama's
default is small and silently truncates the tool definitions. Use at least the
`limit.context` that `opencode.json` declares for the model (32768 for every model there):

```bash
OLLAMA_CONTEXT_LENGTH=32768 ollama serve      # or set it in the service / app environment
```

```powershell
$env:OLLAMA_CONTEXT_LENGTH=32768; ollama serve
```

Playwright's own browsers are **not** needed (`playwright install` is not run); the kit talks to your installed Chrome.

## Offline run

The example scenario targets a Legion build at `http://localhost:3000` that
exposes `/login` and `/manuals`; its form login reads `DEMO_USER` / `DEMO_PASS`.
Copy your reference clip into the kit first (e.g. `voices/mine.wav`; `/clone-voice` records one there for you).

```bash
source .venv/bin/activate
python -m demo_smoke doctor                   # everything ok/MISSING on one line
python -m demo_smoke voice-check --ref voices/mine.wav    # listen to demo-output/audio/voice_check.wav
export DEMO_USER=alice DEMO_PASS=secret       # credentials live in the environment, never on the kit's command line
python -m demo_smoke run scenarios/example-chat-with-manuals.json --out demo-output/chat-with-manuals --ref voices/mine.wav --tts auto --narration template
```

```powershell
.venv\Scripts\activate
python -m demo_smoke doctor
python -m demo_smoke voice-check --ref voice\ref.wav
$env:DEMO_USER='alice'; $env:DEMO_PASS='secret'
python -m demo_smoke run scenarios\example-chat-with-manuals.json --out demo-output\chat-with-manuals --ref voice\ref.wav --tts auto --narration template
```

Every command prints one summary line, writes `<out>/logs/<cmd>.json` (on exit 3
or 4 it always holds `error` and `exit_code`; `doctor`, `check-model` and
`narrate-validate` keep their report next to them), and exits 0 ok / 2 feature failed /
3 tooling error / 4 bad input / 130 interrupted. The individual stages (`dryrun`, `narrate-template`,
`narrate-llm`, `narrate-validate`, `synth`, `record`, `edit`, `verify`) can be
run one at a time; see the CLI table in `ARCHITECTURE.md`.

Your app must already be running at the scenario's `app_url`. Login
credentials come from environment variables named in the scenario (`username_env`,
`password_env`), never from the file.

## Try it on the bundled mock app

No Legion build at hand? The test fixture app is a static "Chat with Manuals"
mock; the whole pipeline runs against it offline with no torch, no LLM and no
credentials. In one terminal:

```bash
cd tests/fixtures/app && python -m http.server 8765 --bind 127.0.0.1
```

```powershell
cd tests\fixtures\app; python -m http.server 8765 --bind 127.0.0.1
```

In another, from the kit directory (`--tts tone` is a synthetic beep; add `--ref voices/mine.wav --tts auto` once Chatterbox is installed):

```bash
python -m demo_smoke run tests/fixtures/scenarios/fixture-pass.json --out demo-output/fixture --tts tone --headless
```

```powershell
python -m demo_smoke run tests\fixtures\scenarios\fixture-pass.json --out demo-output\fixture --tts tone --headless
```

Expect `run: PASS` and `demo-output/fixture/final/fixture-pass.mp4`. `tests/fixtures/scenarios/fixture-fail.json`
shows what a broken feature looks like (exit 2, failing step and console error in `logs/smoke-results.md`).

### Running the tests

```bash
pip install -r requirements-dev.txt      # pytest, ruff
pytest -q                                # unit + headless browser + one end-to-end run (a few minutes)
ruff check .
```

The browser-driving and end-to-end tests use your installed Chrome/Chromium
(`DEMO_SMOKE_CHROME` if it is somewhere unusual; a Playwright cache under
`/opt/pw-browsers` or `~/.cache/ms-playwright` is found too) and skip when none is
found; everything else runs without Chrome. Torch and Chatterbox are never
needed for the tests; the ML backends are exercised through mocks.

## Local model choice (OpenCode agent / `--narration llm`)

The agent's job is trivial, but it needs a model that returns **real tool
calls** reliably, many times in a row. Honest observations:

| Model (Ollama tag) | Params | VRAM / RAM (q4) | Tool calls | Notes |
|---|---|---|---|---|
| `qwen3-coder:30b` | 30B MoE, 3B active | ~19 GB; usable on CPU with 32 GB RAM | reliable | **Recommended.** Fast for its size because only 3B parameters are active. |
| `devstral:24b` | 24B dense | ~14 GB | reliable | Good alternative on 16-24 GB GPUs. |
| `qwen3:14b` | 14B dense | ~9 GB | mostly reliable | For 12-16 GB GPUs. Turn thinking off if it rambles. |
| `qwen3:8b` | 8B dense | ~5 GB | acceptable | Smallest we would use. Expect an occasional retry. |
| `gpt-oss:20b` | 21B MoE | ~13 GB | **unreliable here** | Observed looping on tool calls (re-issuing the same call) in the team's Centurion tests. Listed in `opencode.json` so you can try it; not recommended. |
| Gemma 3 | 4B-27B | 3-17 GB | not usable | No reliable native tool calling in Ollama; the agent path does not work. Fine for prose only. |

Sizes are rough for Ollama's default 4-bit quantizations; add a few GB for
context. If the model returns text instead of a tool call, see Troubleshooting.

`opencode.json` also defines `llama.cpp` (`http://127.0.0.1:8080/v1`) and
`lmstudio` (`http://127.0.0.1:1234/v1`) providers. `llama.cpp/local` means
whatever `llama-server` has loaded (it ignores the model id). LM Studio checks
the id, so its entry carries a `your-model-id` placeholder: replace that key
with the id `check-model --list` prints (see "Validate with a hosted model
first, then go local") and select it with `--model lmstudio/<id>`.

Probe any endpoint before trusting it (`--list` prints the ids the server
serves, `--model` sends one tool-call request and reports PASS/FAIL):

```bash
python -m demo_smoke check-model --base-url http://localhost:11434/v1 --list
python -m demo_smoke check-model --base-url http://localhost:11434/v1 --model qwen3-coder:30b
```

## TTS model choice

| `--tts` | Model | Runs on | Use when |
|---|---|---|---|
| `turbo` | Chatterbox-Turbo, 350M | CUDA, ROCm, Apple MPS (CPU works but slow) | You have a GPU. Best quality/speed; supports tags like `[chuckle]`. |
| `nano` | Chatterbox-Nano, 110M | CPU (about 3x realtime on 8 cores), any GPU | No GPU, or a laptop. **Not in the PyPI releases** (<= 0.1.7); needs the git build below. |
| `classic` | Original Chatterbox, 500M | GPU or CPU (slow) | You want `exaggeration` / `cfg_weight` control for a more dramatic read. |
| `tone` | synthetic beep envelope | anything, no ML deps | Testing the pipeline, CI, or a silent video. |
| `auto` | | | Default: `turbo` when a CUDA/ROCm/MPS device is found; on CPU `nano` when the installed chatterbox has it, else `turbo` (works on CPU, slowly). |

Nano: PyPI's `chatterbox-tts` (latest 0.1.7) ships Turbo and classic only, so
`--tts nano` on that build stops with a clear error and `auto` uses Turbo on CPU.
The Nano model lives on Resemble's git master; to get it, install that instead of
the PyPI wheel (same torch pin, needs `git`):

```bash
pip install "chatterbox-tts @ git+https://github.com/resemble-ai/chatterbox.git@5de7a54aa4e5e2baadb0182dde554908b48b85c2"
python -m demo_smoke prefetch --tts nano
```

`doctor` reports `chatterbox_nano` (true/false) and `tts_auto` (the backend `auto`
resolves to on this machine).

Install torch for your accelerator **before** `pip install -r requirements-tts.txt`
(`chatterbox-tts` 0.1.7 pins `torch==2.6.0` / `torchaudio==2.6.0` on Python <3.14 and
`torch>=2.9` on 3.14, which the kit does not support; the file pins `chatterbox-tts==0.1.7`
and the same torch build so pip cannot swap in a different one). Expect a big install:
0.1.7 pulls in gradio 6.8 (fastapi, uvicorn, pandas, pillow, ...), transformers 5.2,
diffusers, librosa and friends, a few GB and several minutes; on Python <3.13 it also
downgrades numpy to 1.x, which is expected.

```bash
# NVIDIA CUDA 12.x
pip install torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu126   # or cu124
# AMD ROCm (Linux only)
pip install torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/rocm6.2.4
# CPU only (Linux/Windows)
pip install torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cpu
# macOS (Apple Silicon, MPS): the default PyPI wheels
pip install torch==2.6.0 torchaudio==2.6.0
```

`scripts/setup.sh --tts --torch cuda|rocm|cpu|auto` does exactly this
(`--torch-index URL` overrides the index; `DEMO_SMOKE_TORCH_VERSION` the version).

## Capture modes

- `--capture screencast` (default): Chrome's own CDP screencast of the tab.
  Cross-platform, works headless, records only the page (no desktop, no
  notifications), and a cursor overlay is injected so the pointer is visible.
  Use this for web apps.
- `--capture screen`: an OS screen grabber (Windows `gdigrab`, macOS
  `avfoundation`, Linux `x11grab`) over the page area of the Chrome window
  (the tab strip and toolbar are excluded; HiDPI/Retina and Windows display
  scaling are compensated, so the capture is exactly the viewport). Needs a real
  display, captures whatever is on it, and on macOS needs Screen Recording
  permission for your terminal (the main display is recorded; set
  `DEMO_SMOKE_SCREEN_INDEX=1` for a second display). The display must be larger
  than the viewport plus Chrome's tab strip and toolbar (about 90 px): the default
  1920x1080 viewport does not fit a 1080p screen, so use a smaller `viewport` in the
  scenario or `screencast`. On Linux it is X11 only (`x11grab`): in a Wayland session
  (default on current GNOME/KDE) it cannot see a native Wayland Chrome window; use
  `screencast`, or run Chrome under XWayland. Use it only when the demo
  shows something outside the tab (native dialogs, a second window, non-browser apps).

`--headless` runs Chrome with `--headless=new`; combine with `screencast` for
unattended runs on servers without a display.

## The no-LLM path

You do not need a language model at all:

```bash
python -m demo_smoke run scenarios/my-feature.json --narration template --ref voices/mine.wav
```

`--narration template` builds the narration from the scenario's own `intro`,
`outro` and per-step `narration` fields. If you write those fields well, this
path is fully deterministic and the most reliable way to produce a video.

## The OpenCode path

[OpenCode](https://opencode.ai) is the only "agent" here; a local model runs
the same commands for you and writes the narration. Everything it may do is
pinned down in three places:

- `opencode.json` - local providers (`ollama`, `llama.cpp`, `lmstudio`), default
  model `ollama/qwen3-coder:30b`, permissions (web denied, bash limited to the kit's
  commands with `creds set`, `prefetch` and `--online` denied outright, `.env` denied to the
  read tool and to `cat`/`type`/`printenv`/`env`,
  edits limited to `demo-output/**`, `scenarios/*.json` and `narration.json`),
  sharing and auto-update disabled. It sets no `enabled_providers`/`disabled_providers`,
  so the providers you authenticated in OpenCode itself stay available (see the next section).
- `.opencode/agents/demo-smoke.md` - the **single source of truth** for the
  `demo-smoke` agent (mode, temperature, step limit, permissions and the
  playbook prompt; no pinned model, so `--model` and `/models` work). `opencode.json`
  only points to it via `default_agent`; there is no duplicate inline agent definition.
- `.opencode/commands/*.md` - `/setup`, `/smoke`, `/narrate`, `/voice-check`, and the
  interactive `/onboard` and `/clone-voice` (TUI only: they use OpenCode's question tool,
  which `opencode run` denies; see "Onboarding: voice, credentials, scenario").
- `AGENTS.md` - short rules every session loads.

Non-interactive, from the kit directory (`--command <name>` runs a custom
command; the message holds its arguments: scenario, output dir, then optionally
the word `headless` and/or a reference `.wav`):

```bash
export OPENCODE_DISABLE_MODELS_FETCH=1                          # no catalog refresh from models.opencode.ai
export OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS=1200000    # CPU synth / a 90 s recording outlive the default bash timeout
export DEMO_USER=alice DEMO_PASS=secret                         # before starting opencode; the agent never puts them on a command line
opencode run --agent demo-smoke --auto --command smoke "scenarios/example-chat-with-manuals.json demo-output/chat-with-manuals"
opencode run --agent demo-smoke --auto --command smoke "scenarios/x.json demo-output/x headless voices/mine.wav"
opencode run --agent demo-smoke --auto --model ollama/devstral:24b --command smoke "scenarios/x.json demo-output/x"
opencode run --agent demo-smoke --auto --command voice-check "voices/mine.wav"
opencode run --agent demo-smoke --auto --command narrate "scenarios/x.json demo-output/x"
```

(PowerShell: `$env:OPENCODE_DISABLE_MODELS_FETCH=1`, `$env:DEMO_USER='alice'`, and so on.)

`--auto` approves everything that is not explicitly denied (the deny list still
holds: no web, no `prefetch`, no `--online`, no `rm -rf`, no `git push`, no edits
outside the allowed paths, and `doom_loop` is denied so a third identical tool call
is blocked instead of approved). Without `--auto`, anything outside the allow list
prompts you, and in `opencode run` nobody is there to answer, so keep `--auto` for
non-interactive use. The example scenario needs the app and `DEMO_USER` /
`DEMO_PASS` exported in the shell that starts `opencode`, exactly like the CLI run above.

Interactive TUI: run `opencode` in the kit directory. The `demo-smoke` agent is
the default (Tab cycles agents); type `/smoke scenarios/x.json demo-output/x` and
watch (`/narrate` needs both arguments). Use `/models` to switch to
`llama.cpp/local`, `lmstudio/<id>` or a hosted model. `/setup` only checks the
environment and prints the setup command for you to run; the agent never installs
anything. `/onboard` and `/clone-voice` ask you questions (feature, URL, login,
steps; microphone and voice name) and only work here in the TUI. Export
`OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS=1200000` in the shell before running
`opencode`, exactly as for `opencode run`: the TUI has the same 120 s default bash
timeout, and the playbooks set a per-call timeout only for the long commands (synth,
record, record-ref, voice-check).

The playbook in the agent is written for small models: exact commands, one
tool call per step, read the JSON after each command, stop on exit code 2 or 3,
never write code. The mandated `/smoke` sequence is about 16-20 tool calls (8 commands,
a log read after each, the narration write and the venv check) plus one narration
retry. `/onboard` is longer: up to fifteen questions, the login-page and app inspects,
the scenario write, validate and dryrun with their log reads, and the single retries
the command file allows come to 35-45 calls on a bumpy run. The agent's `steps: 60`
covers both with room for the report; at the limit OpenCode forces a text-only answer.

## Validate with a hosted model first, then go local

The pipeline is deterministic; the only variable is whether the model returns
clean tool calls, many times in a row. So prove the kit once with a model you
already trust, then switch to the local one and compare the two runs.
`opencode.json` sets no `enabled_providers`/`disabled_providers` and the
`demo-smoke` agent pins no model, so every provider you have logged into in
OpenCode itself (`opencode auth list`; your global `~/.config/opencode/opencode.json`
merges with the kit's, later files winning per key) stays available next to
`ollama`, `llama.cpp` and `lmstudio`, and `--model provider/model` (or `/models`
in the TUI) picks one per run.

1. Hosted run, with any id `opencode models` prints (the example is Anthropic; use
   whatever you are logged into):

```bash
opencode models                                        # every provider/model id you can use right now
opencode run --agent demo-smoke --auto --model anthropic/claude-sonnet-4-5 --command smoke "scenarios/x.json demo-output/x-hosted"
```

```powershell
opencode models
opencode run --agent demo-smoke --auto --model anthropic/claude-sonnet-4-5 --command smoke "scenarios\x.json demo-output\x-hosted"
```

2. LM Studio run. Start LM Studio's server (Developer tab: **Start server**, port 1234,
   leave "Serve on local network" **off**, turn **JIT model loading** on so the model
   OpenCode names is loaded on first use, and set the model's context length to at
   least 32k in its load settings: `limit.context` in `opencode.json` is only OpenCode's
   budget, it does not change the server). Then list the ids LM Studio actually serves,
   probe one for tool calls, put it into `opencode.json` and run:

```bash
python -m demo_smoke check-model --base-url http://127.0.0.1:1234/v1 --list           # the ids LM Studio serves, copy one
python -m demo_smoke check-model --base-url http://127.0.0.1:1234/v1 --model <id>     # PASS = it returns tool calls
# opencode.json -> provider.lmstudio.models: rename the "your-model-id" key to <id>, then:
opencode run --agent demo-smoke --auto --model lmstudio/<id> --command smoke "scenarios/x.json demo-output/x-local"
```

```powershell
python -m demo_smoke check-model --base-url http://127.0.0.1:1234/v1 --list
python -m demo_smoke check-model --base-url http://127.0.0.1:1234/v1 --model <id>
# opencode.json -> provider.lmstudio.models: rename the "your-model-id" key to <id>, then:
opencode run --agent demo-smoke --auto --model lmstudio/<id> --command smoke "scenarios\x.json demo-output\x-local"
```

   `opencode models lmstudio` also shows a few ids that come from OpenCode's own
   models.dev catalog for the `lmstudio` provider (e.g. `lmstudio/openai/gpt-oss-20b`);
   only the ids `check-model --list` prints are actually loaded on your machine.
   LM Studio's OpenAI endpoint supports tool calling only for models whose chat
   template declares tools (the model card / the tool icon in LM Studio's model list);
   `check-model --model <id>` is the source of truth, not the catalog.

3. Compare the two output directories: both should hold `final/<slug>.mp4` with
   `verify` passing, and the agent's final `Report` says `Narration: written by me`
   when the model wrote a valid `narration.json` or `template` when it fell back.
   The fallback also leaves a file behind, so you can check it after the fact:

```bash
ls demo-output/x-hosted/logs/narrate-template.json demo-output/x-local/logs/narrate-template.json   # exists only where the model fell back to the template
grep -h '"valid"' demo-output/x-hosted/logs/narrate-validate.json demo-output/x-local/logs/narrate-validate.json
```

```powershell
Test-Path demo-output\x-hosted\logs\narrate-template.json; Test-Path demo-output\x-local\logs\narrate-template.json
Select-String '"valid"' demo-output\x-hosted\logs\narrate-validate.json, demo-output\x-local\logs\narrate-validate.json
```

   Without the agent, `python -m demo_smoke run scenarios/x.json --out demo-output/x-llm --narration llm --base-url http://127.0.0.1:1234/v1 --model <id>`
   asks the model for the narration directly; its `report.md` then has a
   `Narration source: llm` or `template` line and `result.json` the
   `narration_source` key, so `grep "Narration source" demo-output/*/report.md`
   (PowerShell: `Select-String "Narration source" demo-output\*\report.md`) compares any number of runs.

### Single-machine resource budget

The intended setup is one Windows laptop running LM Studio (or Ollama), OpenCode,
the kit, Chrome and the app under test, so the LLM and Chatterbox share RAM/VRAM
with Chrome and the app. Load the LLM once and keep it loaded for the whole run
(LM Studio: JIT loading with a long enough auto-unload TTL; Ollama:
`OLLAMA_KEEP_ALIVE=1h`), and let `synth` run while the LLM is idle: the agent's
playbook already orders it that way (narration first, then `synth`, then `record`).
Pick the TTS by GPU: NVIDIA gives `--tts turbo` on CUDA; on Windows with an AMD or
Intel GPU PyTorch has no ROCm, so it is CPU only: `--tts nano` (git build, fast) or
`--tts turbo` on CPU (better quality, slower). `doctor` prints `torch=<device>` and
`tts_auto=<backend>` so you can see which case applies before a run. A model on
another machine (say over Tailscale) is an optional variation: point
`options.baseURL` in `opencode.json` at that host, add it to `no_proxy`, and keep
everything else local.

## Onboarding: voice, credentials, scenario

A new machine needs three things. Each has a CLI you can run yourself and a slash
command that walks you through it; `/onboard` and `/clone-voice` use OpenCode's
question tool, which exists only in the interactive TUI (`opencode`, then the
command), never under `opencode run`.

- **Voice.** `python -m demo_smoke devices` lists microphones (and the screens
  `--capture screen` can grab). `python -m demo_smoke record-ref --out voices/nick.wav [--device N] [--seconds 60]`
  prints a ~150-word reading passage, counts down 3-2-1, records mono 48 kHz, trims
  silence, normalises to -3 dBFS and writes `voices/nick.wav` plus `voices/nick.json`
  (duration, speech seconds, SNR, clipping, warnings; exit 4 when a warning fires, the
  file is still saved). It records through `sounddevice` (`pip install sounddevice`;
  Linux also needs `libportaudio2`) and falls back to ffmpeg's OS audio grabber. Linux
  fallback: the pip ffmpeg (`imageio-ffmpeg`) has no PulseAudio input, so it records
  through ALSA `default`, which needs `pipewire-alsa` (PipeWire) or
  `alsa-plugins-pulseaudio` (PulseAudio); install the distro ffmpeg for `pulse` support.
  `--backend ffmpeg --device N|NAME` hands the device to ffmpeg as is (on macOS `N` is the
  avfoundation audio index); under `--backend auto` a numeric `--device` is a sounddevice
  index and the ffmpeg fallback records from the OS default input.
  `/clone-voice nick` runs devices, asks for the microphone and name, records, runs
  `voice-check` and gives a GOOD / RE-RECORD verdict.
- **Credentials.** `python -m demo_smoke creds set DEMO_PASS` prompts without echo and
  writes `DEMO_PASS=...` to the kit's `.env` (0600 on macOS/Linux; on Windows it inherits the
  kit folder's ACL, so keep the kit under your own user profile, not in a shared folder;
  gitignored; the value may be a
  1Password reference such as `op://vault/item/field`, resolved at run time through the
  `op` CLI). `creds list` prints names only; `creds check DEMO_USER DEMO_PASS` says
  whether each resolves (environment, `.env` or `op://`; exit 4 lists the missing names).
  Every kit command loads the plain values of `.env` at start (a variable already in the
  environment wins); an `op://` reference is resolved only by a login that needs it, in the
  kit process, so `doctor`, `synth` or `record` never unlock the vault and Chrome does not
  inherit the secret (the scenario's credential names are stripped from its environment).
  Credentials are only ever typed into a loopback host (`localhost`, `127.0.0.1`, `::1`): a
  scenario pointing at any other host fails its login unless you export
  `DEMO_SMOKE_ALLOW_REMOTE_LOGIN=1` in your own shell. The agent may run `creds list` /
  `creds check`, but `creds set` is denied for it (it needs your terminal), `.env` is denied
  to its read tool and to `cat`, `type`, `printenv`, `env`, `set`, `export` and `declare`
  (their argument forms too), `.ignore` keeps `.env` out of its grep/glob tools even when
  the kit is not a git checkout, and `--env-file` and the remote-login override are denied
  strings. That deny list is not a sandbox: under `--auto`
  any other bash command is approved, so keep secrets out of the shell that starts
  `opencode` if you do not trust the model. `/onboard` prints the exact `creds set`
  lines for you to run.
- **Scenario.** `python -m demo_smoke init-scenario --name "Chat with Manuals" --url http://localhost:3000 --out scenarios/chat-with-manuals.json --login form --username-env DEMO_USER --password-env DEMO_PASS --step "Open the app :: see the home screen" --step "Ask :: type a question and see a cited answer"`
  writes a valid scaffold whose steps carry a `todo` instead of selectors
  (`--interactive` asks the same questions in the terminal). `python -m demo_smoke inspect http://localhost:3000 [--login-from scenarios/x.json] [--headless]`
  opens the page in Chrome and prints every input, button, link and file input with a
  stable selector, its text, placeholder and label. Fill `actions` / `expect`, delete the
  `todo` keys, then `python -m demo_smoke validate scenarios/x.json` (exit 4 with one
  message per problem; warns about `todo` steps, empty steps and unset credential
  names) and `dryrun`. `/onboard` does all of this with questions and ends with the
  `/smoke` line to run next.

## Writing a scenario

Copy `scenarios/example-chat-with-manuals.json` and edit. The full format
(login types, every action and expectation, timing model) is in
[`ARCHITECTURE.md`](ARCHITECTURE.md); `scenarios/schema.json` is a JSON Schema
for editor autocompletion. The short version:

```json
{
  "name": "Chat with Manuals", "slug": "chat-with-manuals",
  "app_url": "http://localhost:3000", "viewport": {"width": 1920, "height": 1080},
  "login": {"type": "none"}, "max_length_seconds": 90,
  "intro": "One sentence spoken over the first screen.",
  "outro": "One sentence at the end.",
  "steps": [
    {"id": "open", "title": "Open the app", "narration": "I open the app from the home screen.",
     "actions": [{"goto": "/"}], "expect": [{"text": "Chat with Manuals"}], "timeout_s": 30}
  ]
}
```

Actions: `goto`, `click`, `fill`, `type`, `press`, `upload`, `hover`, `scroll`,
`wait`, `wait_for`, `screenshot`. Expectations: `text`, `selector` (+ `contains`,
`count_min`), `url_contains`, `not_text`. Relative file paths resolve against the
scenario file. Keep each step's narration under 45 words; the total word budget
is `max_length_seconds x 2.6`. Run `dryrun` first; it is fast and tells you
which step and which expectation failed, with console errors.

## Outputs

`<out>` defaults to `demo-output/` (use `--out demo-output/<slug>` to keep runs apart):

```
<out>/
  report.md, result.json            verdict, step table, checks, artifact paths, env (written by `run` only)
  final/<slug>.mp4                  the deliverable (H.264 + AAC, faststart)
  final/thumb-10.png, -50, -90      thumbnails
  logs/<cmd>.json                   machine-readable result of every command (always with "error" and "exit_code" on exit 3/4)
  logs/scenario.json                the loaded scenario (used by edit/verify/narrate-validate without SCENARIO)
  logs/step-NN-<id>.png             screenshot after each dryrun step; logs/record-NN-<id>.png during record
  logs/<name>.png                   extra `screenshot` actions
  logs/failure-<id>.html            page DOM at the failing step (password inputs emptied)
  logs/smoke-results.md             human smoke report (failures, console errors)
  logs/dryrun.json, markers.json, edit.json (exact ffmpeg command), edit-filter.txt, verify.json
  logs/chrome.log, ffmpeg-capture.log / screen-capture.log
  audio/narration.json, seg-*.wav, durations.json, synth-stats.json, voice_check.wav
  raw/capture.mp4                   unedited capture; raw/frames/ + frames.json + frames.txt (ffconcat list) for the screencast backend
  chrome-profile/                   Chrome's fresh profile while a command runs; removed on close (it holds the app session's cookies)
  clips/                            reserved, always empty
```

Outside `<out>`: `voices/<name>.wav` + `voices/<name>.json` from `record-ref`, `.env`
from `creds set` (both gitignored), and `scenarios/<slug>.json` from `init-scenario`.

## Sharing the MP4

The kit never uploads anything. Offline, drop `final/<slug>.mp4` on a file
share, USB stick, or attach it to the ticket; it is a standard MP4 (yuv420p,
faststart) that plays in every browser and in Slack/Teams previews. If you
later want a link, upload it by hand to Loom, YouTube (unlisted), or your
company's video host; the file needs no re-encoding.

## Troubleshooting

- **The model answers in prose instead of calling tools / loops on the same call.**
  Almost always context length: start Ollama with `OLLAMA_CONTEXT_LENGTH` at least
  the model's `limit.context` from `opencode.json` (32768; or set `num_ctx` in a
  Modelfile). `limit.context` only tells OpenCode the budget, it does not change the
  server, and a server context smaller than the budget silently truncates the tool
  definitions. Then try a different model (`qwen3-coder:30b`, `devstral:24b`);
  `gpt-oss:20b` and Gemma 3 are known problems.
  `python -m demo_smoke check-model --base-url ... --model ...` isolates the issue.
- **`chatterbox` errors offline** (`Cannot reach huggingface.co`, `OfflineModeIsEnabled`,
  missing snapshot): run the `python -m demo_smoke prefetch --tts <the backend named
  in the error>` command the message quotes, while online, once per model. The cache
  is `HF_HUB_CACHE` if set, else `HUGGINGFACE_HUB_CACHE`, else `$HF_HOME/hub` (default
  `$XDG_CACHE_HOME/huggingface/hub`, i.e. `~/.cache/huggingface/hub`); `doctor` prints the
  path and `tts_ready` (yes only when every weight file of the chosen backend is in the
  snapshot, so an interrupted prefetch shows NO). Copy that folder to move it to an offline box.
  `--online` on `synth`/`voice-check` allows downloads for one run.
- **`--tts nano` says the installed chatterbox has no Nano model**: PyPI's release
  has none; install the git build (see "TTS model choice") or use `--tts turbo`.
- **`chrome MISSING`**: install Google Chrome, or set `DEMO_SMOKE_CHROME` to the
  binary (`/usr/bin/google-chrome`, `C:\Program Files\Google\Chrome\Application\chrome.exe`,
  `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`, or a Chromium).
  `doctor` says so when `DEMO_SMOKE_CHROME` points at a file that does not exist.
- **LLM `UNREACHABLE` although the server runs**: a corporate `HTTP_PROXY` is set.
  The kit never proxies loopback addresses; for any other host add it to `no_proxy`.
- **`ffmpeg MISSING`**: `pip install imageio-ffmpeg` in the venv, or install ffmpeg
  and/or set `DEMO_SMOKE_FFMPEG`. No `ffprobe` is fine; the kit parses `ffmpeg -i`.
- **ROCm**: use the `rocm6.2.4` index shown above (Linux only; Windows has no ROCm
  torch wheels, use CPU + `nano`). `doctor` reports `torch=rocm` when the HIP
  build is active. If the GPU is unsupported, `HSA_OVERRIDE_GFX_VERSION` is the
  usual workaround; otherwise `--tts nano` on CPU.
- **Apple Silicon**: default PyPI torch gives MPS; `auto` picks `turbo`. If MPS
  falls over on an op, `PYTORCH_ENABLE_MPS_FALLBACK=1`.
- **Windows Security toast "Smart App Control has blocked part of this app"** (naming a
  `.pyd` such as `timezones.cp311-win_amd64.pyd`, `torch\lib\...dll` or a librosa/numba
  module) during setup or the first `--tts turbo`/`nano` run: Windows 11's Smart App
  Control blocks unsigned/unknown binaries and has no allowlist, so the Python extension
  modules torch, pandas and librosa ship cannot load. `python -m demo_smoke doctor` prints
  `smart_app_control=ON` and a `PROBLEMS` hint (it reads
  `HKLM\SYSTEM\CurrentControlSet\Control\CI\Policy\VerifiedAndReputablePolicyState`,
  1 = on, 2 = evaluation, 0 = off; `scripts\bootstrap-tablet.ps1` warns the same way and
  continues). Fix: Windows Security > App & browser control > Smart App Control settings >
  **Off** (one-way: it cannot be turned back on without reinstalling Windows), then re-run
  `scripts\setup.ps1`; or run the kit inside WSL2 instead. `--tts tone` and the browser
  pipeline work either way (no native extensions involved).
- **Windows: "running scripts is disabled on this system"**: either
  `powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 ...` or, once,
  `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. Use `.venv\Scripts\python.exe`
  if `python` resolves to the Microsoft Store stub.
- **`record` works but the video is black / wrong window** with `--capture screen`:
  the grabber caught a different display or lacked permission; use `screencast`,
  or on macOS set `DEMO_SMOKE_SCREEN_INDEX` to the display Chrome opened on.
- **Feature FAIL (exit 2)**: that is the point of the kit. `logs/smoke-results.md`
  lists the failing expectation, console errors and failed requests;
  `logs/step-NN-<id>.png` shows the screen at that moment and
  `logs/failure-<id>.html` is the page DOM at the failing step.
- **`/onboard` or `/clone-voice` says it needs the TUI**: the question tool is denied
  under `opencode run`; start `opencode` (no arguments) in the kit directory and type
  the command there. **`creds set` is refused for the agent**: that is intended, run
  `python -m demo_smoke creds set NAME` in your own terminal (the value is prompted
  without echo and lands in `.env`, which the agent's read tool and `cat`/`type` are denied).
- Environment variables the CLI reads: `DEMO_SMOKE_CHROME`, `DEMO_SMOKE_FFMPEG`,
  `DEMO_SMOKE_FFPROBE`, `DEMO_SMOKE_BASE_URL` / `DEMO_SMOKE_MODEL` (defaults for
  `--base-url` / `--model`), `DEMO_SMOKE_API_KEY` (bearer token for the LLM endpoint, always
  sent) or `OPENAI_API_KEY` (sent only to `https://` or non-loopback endpoints, so a key
  exported for other tools never reaches a plain-http local server; `doctor`'s probes of the
  local ports carry no token at all), `DEMO_SMOKE_SCREEN_INDEX`, `HF_HUB_CACHE` / `HUGGINGFACE_HUB_CACHE` /
  `HF_HOME` (that order), and `DEMO_SMOKE_DEBUG=1`, which turns one-line errors back into full
  tracebacks. For OpenCode itself: `OPENCODE_DISABLE_MODELS_FETCH=1` and
  `OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS` (see "The OpenCode path").

### Seen on real Windows machines

- **Chrome exits immediately, empty `logs/chrome.log`, DevTools never answers.** Chrome 152
  on Windows exits 0 when `--user-data-dir` is a relative path. The kit now resolves the
  output directory to an absolute path before launching, so a relative `--out` is fine.
- **`Unrecognized option 'filter_complex_script'` during `edit`.** ffmpeg 9 removed that
  flag. The kit picks the spelling by ffmpeg version (`-/filter_complex FILE` on 7+,
  `-filter_complex_script` before). `doctor` prints the ffmpeg version it will use; a
  system ffmpeg on PATH wins over the bundled imageio-ffmpeg one, set `DEMO_SMOKE_FFMPEG`
  to choose explicitly.
- **`tts_ready=NO` although `prefetch` finished.** Turbo/Nano snapshots ship
  `tokenizer_config.json` + `vocab.json` + `merges.txt` instead of `tokenizer.json`; the
  readiness check accepts either now.
- **Low disk.** `doctor` prints `disk_free=<GB>G` for the drive holding the HF cache and
  warns under 10 GB. Weights are 2-5 GB per backend; each run's `demo-output/<slug>` is a few
  hundred MB (screencast frames are deleted after assembly, the Chrome profile after close).
- **Smart App Control.** See the warning `doctor` prints; it is a one-way switch.

## Privacy and consent

- Voice cloning: only clone a voice whose owner has agreed to it, and say in the
  video that the narration is synthetic when the audience might not expect it.
- Every WAV Chatterbox produces carries Resemble AI's imperceptible **Perth**
  watermark, which survives into the MP4. Detectable with the `perth` package;
  this is by design and the kit does not remove it.
- The kit itself sends nothing anywhere: no telemetry, `HF_HUB_OFFLINE=1` is
  exported before Chatterbox loads, OpenCode sharing and auto-update are disabled in
  `opencode.json`, and the agent is denied web access, `prefetch` and `--online`.
  OpenCode does refresh its model catalog from `models.opencode.ai` on start unless
  `OPENCODE_DISABLE_MODELS_FETCH=1` is set (it is an environment flag, not a config
  key; the README examples export it), and it downloads ripgrep from GitHub the first
  time grep/glob/file search runs without an `rg` on PATH or in its cache (see "Online
  preparation"). The reference clip stays where you recorded
  it (`voices/`, gitignored via `*.wav`); screenshots and recordings stay under
  `demo-output/` (gitignored).

## Benchmark: local vs hosted vs manual

`bench` runs the **same scenario** under several drivers, collects hard numbers per
run, and writes one side-by-side table so a local model can be compared with a hosted
one and with the manual Codex/Claude runs you did by hand. Every driver run is a normal
kit output directory (`DIR/runs/<driver-slug>/r<N>/` with `report.md`, `result.json`,
`logs/`, `final/`) plus a `bench.json` of metrics; the summary is `DIR/report.md` +
`DIR/bench.json`. The bench never edits the scenario.

Drivers (repeatable; default `template` when none is given): `--driver template` (no LLM
at all: the baseline of the pipeline itself), `--driver 'llm:<base-url>|<model>'` (narration
from an OpenAI-compatible endpoint, pipeline via `run`; quote the spec, `|` is a shell pipe in
bash and PowerShell alike), `--driver opencode:<provider/model>`
(the full agent path:
`opencode run --agent demo-smoke --auto --model <provider/model> --format json --command smoke ...`),
or `--driver opencode:<provider/model>@<base-url>` to point that provider at another
OpenAI-compatible endpoint (its baseURL and model id are overridden for that run only).
`--repeat N` runs each driver N times (the table shows the mean, the appendix every run).
`--record-screen` records the **whole display** while each driver runs, one clip per run
(`DIR/meta/clips/NN-<slug>-rN.mp4`; `gdigrab -i desktop` / `avfoundation "Capture screen N:none"`
/ `x11grab` on `$DISPLAY`, screen 0, `:0.0` when DISPLAY is unset; 15 fps; needs a visible
desktop, so not under `--headless` in a container), `--meta-narrate` then builds
`DIR/meta/<slug>-bench.mp4`: the screen recordings concatenated, with a factual narration in
the cloned voice (intro, one segment per driver with minutes, tool calls, verdict and narration
source, an outro against every baseline entry; the template driver is quoted as the
pipeline-only baseline, never as the "fastest driver"). `--baseline bench/baseline.json` merges
manual rows into the same table, flagged "manual"; the file does not ship - copy
`bench/baseline.example.json` to `bench/baseline.json` and edit it (it is kept by git on
purpose, so your manual runs travel with the repo; drop the `!bench/baseline.json` line from
`.gitignore` if you want it private), or point `--baseline` at the example. Optional numeric
keys of an entry use the bench's units: `total_minutes` minutes, `video_seconds` seconds,
`references_on_screen` a fraction 0..1, `tool_calls` / `narration_words` / `validation_retries`
/ `tokens_total` counts, `cost` USD (a wrong one is rejected as bad input).
Other flags: `--tts` and `--ref` are passed to every run (`run --tts` for the template/llm
drivers; an OpenCode agent gets a `tts:<backend>` token in its smoke command and uses it in
`synth`, except that its playbook still switches to `tone` when doctor finds no usable TTS),
`--headless` to dryrun/record,
`--opencode-bin PATH` picks the binary (else `OPENCODE_BIN`, then PATH), `--timeout-s N`
bounds each run (default 3600; the whole driver process tree is killed) and
`--llm-timeout N` is the llm driver's per-request timeout (default 180, so the kit's
template fallback still gets its turn); `bench-meta` also takes `--bench-json PATH` and
`--online`.

bash (Linux / macOS / WSL2; replace the LM Studio model id with one `check-model --list` prints):

```bash
export OPENCODE_DISABLE_MODELS_FETCH=1   # only needed when you run opencode by hand; bench sets it for its own driver processes
cp bench/baseline.example.json bench/baseline.json   # then edit: your own manual runs
python -m demo_smoke bench scenarios/x.json --out demo-output/bench \
  --driver template \
  --driver 'llm:http://127.0.0.1:1234/v1|openai/gpt-oss-20b' \
  --driver opencode:lmstudio/openai/gpt-oss-20b \
  --driver opencode:anthropic/claude-sonnet-4-5 \
  --ref voices/ref.wav --record-screen --meta-narrate \
  --baseline bench/baseline.json
# rebuild only the meta video from an existing bench directory (e.g. with another voice):
python -m demo_smoke bench-meta --out demo-output/bench --ref voices/ref.wav --tts auto \
  --baseline bench/baseline.json
```

PowerShell (Windows):

```powershell
$env:OPENCODE_DISABLE_MODELS_FETCH = "1"   # only needed when you run opencode by hand; bench sets it for its own driver processes
Copy-Item bench\baseline.example.json bench\baseline.json   # then edit: your own manual runs
python -m demo_smoke bench scenarios\x.json --out demo-output\bench `
  --driver template `
  --driver 'llm:http://127.0.0.1:1234/v1|openai/gpt-oss-20b' `
  --driver opencode:lmstudio/openai/gpt-oss-20b `
  --driver opencode:anthropic/claude-sonnet-4-5 `
  --ref voices\ref.wav --record-screen --meta-narrate `
  --baseline bench\baseline.json
# rebuild only the meta video from an existing bench directory:
python -m demo_smoke bench-meta --out demo-output\bench --ref voices\ref.wav --tts auto `
  --baseline bench\baseline.json
```

`bench-meta` reads `DIR/bench.json` (`--out`, default `demo-output/bench`), takes the clips
from `DIR/meta/clips/*.mp4` (name order = run order; `--clips a.mp4 b.mp4` overrides), writes `DIR/meta/narration.json`,
`DIR/meta/audio/seg-*.wav` and `DIR/meta/<slug>-bench.mp4`, and logs the plan and the
exact ffmpeg command to `DIR/meta/meta-edit.json` + `DIR/logs/bench-meta.json`. Segments
are placed one after another with a 0.5 s gap (`--gap`); when the narration is longer
than the recordings the last frame is held, and `--align-clips` starts each driver's
segment no earlier than that driver's own recording (matched by the `<slug>` in the clip
name, so `--repeat` runs of one driver share its segment; a note is logged when the option
cannot be applied). `--tts tone` uses the obviously synthetic
placeholder tone (no ML deps) for a dry run. `--baseline` takes the same file (list or
`{"entries": [...]}`) as `bench`; without it the entries stored in `bench.json` are spoken.

`python -m demo_smoke opencode-events EVENTS.jsonl [--out DIR] [--json]` summarises a saved
`opencode run --format json` stream (tool calls, kit commands, steps, tokens, final status).
For each agent run bench keeps the raw stream as `runs/<slug>/rN/logs/bench-stdout.txt` (pass
that file to `opencode-events`); `runs/<slug>/rN/logs/opencode-events.json` is the parsed summary.

**What the numbers do and do not mean.** A `template` row always "passes" when the app
works: it proves the pipeline, not a model, and its narration is the scenario's own text.
Minutes are the wall time of the whole driver process (Python/torch start-up, TTS model
load, OpenCode start-up and the model's warm-up included), averaged over a driver's PASS
runs (a row with no passing run shows the mean over every run instead, and `pass_minutes` is
null, so a FAIL/ERROR row's minutes include timed-out runs; a run the bench had to kill on its
timeout is ERROR even when it had delivered a video), so rows are comparable with each other
but not with a stopwatch on the agent alone; every other mean in a row covers the same PASS
runs and is marked `(k/n)` when it came from k of the n runs;
tool calls (every call, file reads included; the appendix has the kit-command count),
validation retries and the on-screen-reference fraction are mechanical, but the last one
is a sanity check that the template scores by construction, not a quality score;
narration *quality* is not measured - only a human ear (and the MP4 next to each row)
can judge whether a model's sentences match what is on screen. Hosted models cost money on every run (the tokens come from
OpenCode's events; the cost is OpenCode's own catalog estimate, not a provider figure, and is
left blank when OpenCode has no price for the model - every `@<base-url>` override - rather
than shown as `$0.0000`) and send your scenario, page text and screenshots
off the machine; local runs are free and stay offline but are slower and more often fall
back to the template narration. Manual baseline rows are what a person wrote down, not a
measurement, so treat them as context. One run is an anecdote: use `--repeat` before
drawing conclusions.
