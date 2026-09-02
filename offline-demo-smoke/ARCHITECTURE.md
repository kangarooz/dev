# Offline Demo Smoke Kit — architecture contract

Goal: run a feature smoke test against a local app (e.g. a Centurion / Legion
build), record a narrated walkthrough in a cloned voice, and produce an MP4,
fully offline, with OpenCode + a local model as the only "agent", and with no
subscription services. The LLM only (a) runs the exact commands below and
(b) writes narration text. Everything else is deterministic Python.

All paths below are relative to this directory (the "kit"). Python 3.10+.

## Layout

```
offline-demo-smoke/
  ARCHITECTURE.md            this file
  README.md                  install / run / offline checklist / model table / troubleshooting
  AGENTS.md                  rules OpenCode loads for the local model
  opencode.json              local providers (ollama default; llama.cpp, lmstudio), permissions, agent, commands
  .opencode/agents/demo-smoke.md
  .opencode/commands/{setup,smoke,narrate,voice-check}.md
  .opencode/commands/{onboard,clone-voice}.md   onboarding widget (question tool; TUI only)
  voices/                    reference voices from record-ref (<name>.wav + <name>.json, gitignored; .gitkeep kept)
  .env                       credentials written by `creds set` (gitignored, 0600); loaded by cli.main at start-up
  .ignore                    re-includes demo-output/ for ripgrep/OpenCode (gitignore hides it)
  requirements.txt           playwright, numpy, soundfile, imageio-ffmpeg, sounddevice (record-ref; ffmpeg fallback)
  requirements-tts.txt       chatterbox-tts + torch/torchaudio 2.6.0 (see README for index URLs)
  requirements-dev.txt       pytest, ruff
  scripts/setup.sh, scripts/setup.ps1
  scenarios/schema.json, scenarios/example-chat-with-manuals.json, scenarios/fixtures/osha-1910.pdf
  bench/                     baseline.example.json (+ your baseline.json, both tracked); runs/, meta/, report.md,
                             bench.json written by `bench --out bench/...` are gitignored
  demo_smoke/                python package (see modules)
  tests/                     pytest; tests/fixtures/app/ is a static mock app
  demo-output/               default OUTPUT_DIR (gitignored)
```

Contract corrections (review round 1): `auto` TTS no longer assumes Nano on
CPU (the PyPI chatterbox-tts has none); `record` fails on a failed login;
`selector` expectations count visible elements; `detect()` takes `timeout`;
`run_steps()` takes `do_login`; capture classes expose `abort()`; the CLI
table now lists every implemented flag.

## CLI contract  (`python -m demo_smoke <cmd> ...`)

Every command prints a one-line human summary to stdout, writes JSON to
`<out>/logs/<cmd>.json`, and uses exit codes: 0 ok, 2 feature failed
(smoke test FAIL), 3 pipeline/tooling error, 4 bad input, 130 interrupted (Ctrl-C).

Every command accepts `--out DIR` (default `demo-output`; `bench` requires `--out`, `bench-meta`
defaults to `demo-output/bench`); `python -m demo_smoke --version` exists.
On exit 3 or 4 `<out>/logs/<cmd>.json` contains `"error": msg` and `"exit_code": N`; for most
commands that is the whole file, while `doctor`, `check-model` and `narrate-validate` keep their
report (`hints`, `pass`/`detail`, `errors`/`budget`) next to those two keys. `run` writes its own
`logs/run.json` + `report.md`/`result.json` for every outcome that gets past input validation
(on exit 4, e.g. a bad scenario or `--narration llm` without its flags, only `logs/run.json`
with `error`/`exit_code`). (Review round 2: the earlier
wording "is `{"error", "exit_code"}`" was wrong for those three commands, which never wrote the
keys at all; now they always do.)

| cmd | args | output |
|---|---|---|
| `doctor` | `[--base-url URL --model NAME] [--timeout N]` | env report: os, python, ffmpeg path+version, chrome path, torch device (cuda/rocm/mps/cpu/none), chatterbox importable + `chatterbox_nano`, HF cache path + `hf_weights` per backend, `tts_auto` (what `--tts auto` resolves to here) + `tts_ready`, `tts_advice` (which backend fits this box: CUDA → turbo, Windows CPU → nano/turbo on CPU, ...), `local_endpoints` (ollama :11434, lmstudio :1234, llama.cpp :8080 probed with a 2 s timeout; `reachable` + their model ids, printed one line each as `llm: <name> <url> reachable: id, id` / `not running`), ollama/OpenAI-compatible endpoint reachable, optional tool-call probe; on Windows `smart_app_control` (`on`/`evaluation`/`off`/`unknown`, `None` elsewhere) and `PROBLEMS smart_app_control ON` + the turn-it-off hint when it is on |
| `check-model` | `--base-url URL (--model NAME \| --list) [--timeout N]` | sends a chat completion with one tool (`get_step_status`) and checks the model returns a tool call; PASS/FAIL. `--list` instead prints the ids from `GET /v1/models` (one per line, e.g. the exact LM Studio id to copy into `opencode.json`) and logs `{"list": true, "models": [...]}`; exit 3 when the server cannot be reached, exit 4 when neither `--model` nor `--list` is given |
| `prefetch` | `--tts auto\|turbo\|nano\|classic` | downloads Chatterbox weights into the HF cache (online step; `auto` resolves like `run --tts auto` on this machine, logged as `backend`); prints cache dir |
| `voice-check` | `[--ref REF.wav] --out DIR --tts auto\|turbo\|nano\|classic\|tone [--online]` (omit `--ref` to use the model's default voice) | `audio/voice_check.wav` + stats (duration, peak dBFS, rms dBFS, silent, clipped) |
| `dryrun` | `SCENARIO --out DIR [--headless]` | drives steps, `logs/step-NN-<id>.png`, `logs/smoke-results.md`, `logs/dryrun.json`; exit 2 on FAIL |
| `narrate-template` | `SCENARIO --out DIR` | `audio/narration.json` from scenario `intro`/`outro`/step `narration` fields |
| `narrate-llm` | `SCENARIO --out DIR --base-url URL --model NAME [--timeout N]` | asks the local model for narration JSON, validates, falls back to template on any failure (and says so) |
| `narrate-validate` | `[SCENARIO] --out DIR [--max-seconds N]` | validates `audio/narration.json` (parseable JSON, ids match scenario, word budget); exit 4 on invalid (a file that is not JSON counts as invalid, not as a tool error); SCENARIO defaults to `logs/scenario.json` saved by an earlier command |
| `synth` | `--out DIR [--ref REF.wav] --tts ... [--online]` (omit `--ref` to use the model's default voice) | `audio/seg-intro.wav`, `audio/seg-<id>.wav`, `audio/seg-outro.wav`, `audio/durations.json` |
| `record` | `SCENARIO --out DIR --capture screencast\|screen [--headless]` | paced run: `raw/capture.mp4`, `logs/markers.json`; exit 2 when login failed or any step did not PASS |
| `edit` | `[SCENARIO] --out DIR` | `final/<slug>.mp4` |
| `verify` | `[SCENARIO] --out DIR` | `logs/verify.json`, `final/thumb-{10,50,90}.png`; exit 2 on failed checks |
| `run` | `SCENARIO --out DIR [--tts ...] [--capture ...] [--narration template\|llm] [--ref REF] [--online] [--headless] [--base-url --model --timeout N]` | whole pipeline in order: doctor → dryrun → narrate → synth → record → edit → verify → report; `report.md`, `result.json`; `--narration llm` without `--base-url`/`--model` is bad input (exit 4) before anything runs |

Onboarding commands (added with the onboarding widget; `demo_smoke/onboard_audio.py`,
`demo_smoke/onboard_scenario.py`, `demo_smoke/dotenv.py`). They register themselves on the
same parser (`register(subparsers, run_map)`), never raise past their handler (exit codes are
returned; `DEMO_SMOKE_DEBUG=1` re-raises) and follow the exit-code table above.

| cmd | args | output |
|---|---|---|
| `record-ref` | `--out voices/<name>.wav [--seconds 60] [--device N\|name] [--backend auto\|sounddevice\|ffmpeg] [--list-devices] [--script-only] [--no-countdown]` | prints the reading passage (`demo_smoke/passage.txt`, ~150 words in 3 chunks), the backend is resolved (and the input opened once - a PortAudio stream, or a 0.5 s ffmpeg warm-up capture that also moves the first format that works to the front - so the macOS microphone prompt appears) before the passage and the 3-2-1 countdown; records mono 48 kHz PCM16 (`auto` = sounddevice, retried once at the device's native rate and resampled, then the ffmpeg fallback: `dshow` / `avfoundation` (`:default` = the system default input) / `pulse` then `alsa`; under `auto` a numeric `--device` is a sounddevice index and is not forwarded to the ffmpeg fallback, while `--backend ffmpeg` hands `--device` to ffmpeg as is; a sounddevice failure mid-take falls back to ffmpeg with a fresh countdown), peak-normalises to -3 dBFS, trims leading/trailing silence (-40 dBFS), writes the WAV + a sidecar `voices/<name>.json` (**not** `<out>/logs/`; `--out` is a file) with `duration, peak_dbfs, rms_dbfs, noise_floor_dbfs, speech_seconds, snr_db, clipped_pct, clipped, gain_db, raw_peak_dbfs, native_sample_rate, trim, warnings[]`. Exit 4 with warnings `silent (no signal)` (raw peak below -60 dBFS: muted input, wrong device or a denied microphone permission; a targeted hint is printed) / `too short (<20 s of speech)` / `noisy (SNR < 15 dB)` / `clipped` (file still saved) or on bad input (`--out` not `.wav`, `--seconds <= 0`); exit 3 when no backend could record (nothing written); 130 on Ctrl-C. `--device N` = sounddevice index; a non-integer device name forces ffmpeg; `native_sample_rate` is the rate the backend really captured at (ffmpeg included) |
| `devices` | `[--out DIR]` | audio inputs (sounddevice, or "unavailable") and screens for `--capture screen` (Windows: `desktop (gdigrab)` as index 0 + dshow "screen" devices; macOS: avfoundation `Capture screen N`; Linux: `$DISPLAY`); `logs/devices.json`; always exit 0 |
| `creds set NAME` | `[--env-file PATH] [--value-from-stdin]` | `getpass` prompt (never echoed) or one stdin line; writes/updates `NAME=value` in `.env` (created 0600 on POSIX, duplicates collapsed, values quoted when needed); an `op://vault/item/field` value is stored verbatim and resolved at run time. Names must match `[A-Z_][A-Z0-9_]*` (exit 4 otherwise) |
| `creds list` | `[--env-file PATH]` | names only, never values |
| `creds check NAME...` | `[--env-file PATH]` | each name: `ok (environ\|.env\|op://)` or `MISSING (reason)`; exit 4 when any is missing. An `op://` value counts as MISSING when `op` is not on PATH or `op read` fails (reason printed) |
| `init-scenario` | `--name "..." --url URL [--out scenarios/<slug>.json] [--login none\|form\|basic] [--username-env NAME --password-env NAME] [--login-url --username-selector --password-selector --submit-selector --success-selector] [--step "Title :: plain English" ...] [--interactive] [--force]` | writes a scaffold: intro/outro drafted from the name, one step per `--step` with `actions: []`, `expect: []` and a `todo` field holding the plain-English description. `todo` is not a scenario key, so `dryrun`/`record` reject the file until every todo is resolved and removed (the intended hand-off); `--interactive` asks for missing answers with `input()`. `--out` is the JSON file (stored as `args.scenario_out`, no `logs/` tree) |
| `validate` | `SCENARIO [--out DIR] [--env-file PATH]` | strips `todo` from steps, runs `scenario.validate`, prints the step list; `warning:` lines for steps that still carry `todo`, have no actions or no expectations (exit 0); `validate: INVALID` + one `error:` line per problem (exit 4). `logs/validate.json` only when `--out DIR` is given (the result is otherwise stdout only) |
| `inspect` | `URL [--login-from SCENARIO] [--headless] [--json] [--all] [--max N] [--settle-ms N] [--out DIR] [--env-file PATH]` | opens the page in Chrome and prints a compact table of inputs/buttons/links with a stable, unique selector each (`#id` → `tag[name=..]` → `tag[placeholder=..]` → `input[type=file]` → `button:has-text("..")` → `[aria-label=..]` → `tag >> nth=N`; non-unique candidates shown as `(xN)`), ids, names, placeholders, text; hidden elements skipped except file inputs; 60 rows max (links dropped first). `--login-from` logs in with that scenario's login block first (exit 2 on a failed login). `logs/inspect.json` (`--json` prints the same dict) |

`.env` loading: `cli.main` calls `dotenv.load_env(f, resolve_refs=False)` before building the
parser: `--env-file` (pre-scanned from argv) first, then `<kit>/.env`; only names not already
in `os.environ` are exported (the environment wins), so `DEMO_SMOKE_BASE_URL` /
`DEMO_SMOKE_MODEL` in `.env` also satisfy the required flags. `op://` values are **deferred**
(`load_env.deferred`, name -> reference; a raw `op://` string never reaches `os.environ`) and
resolved by `dotenv.credential(name)` only when `drive.login` needs that name, with `op read`
(full path from `shutil.which`, so `op.cmd`/`op.exe` work on Windows), cached in the kit
process and never exported: `doctor`/`synth`/`record` never unlock the vault, and Chrome and
ffmpeg do not inherit the secret. With `resolve_refs=True` (the default for other callers)
references are resolved and exported at load time and `load_env.unresolved` names failures.
`--help` / `--version` and the `creds` subcommands skip the load (`creds` takes `--env-file`
and resolves on its own: `creds check` reports the real source and `creds set` never
unlocks a vault to store a name). `drive.login` refuses a login block without both names, an
`op://` value that reached the environment unresolved, and any non-loopback `app_url` /
`login.url` unless `DEMO_SMOKE_ALLOW_REMOTE_LOGIN=1` (a scenario the agent may write must not
be able to post the credentials elsewhere); `chrome.launch(env_omit=credential_names(scen))`
keeps the two names out of Chrome's environment.

Env overrides: `DEMO_SMOKE_CHROME` (chrome binary), `DEMO_SMOKE_FFMPEG`
(ffmpeg binary), `DEMO_SMOKE_FFPROBE`, `DEMO_SMOKE_BASE_URL` / `DEMO_SMOKE_MODEL`
(defaults for `--base-url` / `--model`, also where the flag is otherwise required),
`DEMO_SMOKE_API_KEY` (bearer token for the LLM endpoint, always sent) or `OPENAI_API_KEY`
(sent only to https or non-loopback endpoints; `probe_local_endpoints` sends no token),
`DEMO_SMOKE_SCREEN_INDEX` (macOS display for `--capture screen`, default 0),
`DEMO_SMOKE_DEBUG=1` (full tracebacks), `HF_HUB_CACHE` / `HUGGINGFACE_HUB_CACHE` /
`HF_HOME` (Hugging Face cache, resolved in that order like huggingface_hub).
Offline: the kit sets `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` at the start of
`voice-check`, `synth` and `run` (before any probe could import chatterbox, and with it
huggingface_hub, which freezes the variable at import time) unless `--online` is passed;
`doctor`/`resolve_backend` never import chatterbox (the Nano probe reads `tts_turbo.py` as a file). Loopback HTTP (DevTools, local
LLM) never goes through `HTTP_PROXY`.

ffmpeg discovery order: `DEMO_SMOKE_FFMPEG` → `ffmpeg` on PATH →
`imageio_ffmpeg.get_ffmpeg_exe()`. ffprobe: `DEMO_SMOKE_FFPROBE` → PATH →
if absent, use `ffmpeg -i` parsing via a small helper (imageio-ffmpeg ships
no ffprobe). Chrome discovery: env → common per-OS install paths → PATH
names (`google-chrome`, `chrome`, `chromium`, `chromium-browser`) → a Playwright
browser cache (`/opt/pw-browsers`, `~/.cache/ms-playwright`, `~/Library/Caches/ms-playwright`,
`%LOCALAPPDATA%\ms-playwright`), both the legacy `chrome-linux/chrome` layout and the
Chrome-for-Testing layout (`chrome-linux64`, `chrome-mac-*`, `chrome-win64`).

## Modules (package `demo_smoke`)

- `env.py` — OS/ffmpeg/chrome/torch-device discovery, `chatterbox_nano_supported()` (import-free source scan), HF cache dir (`HF_HUB_CACHE` → `HUGGINGFACE_HUB_CACHE` → `$HF_HOME/hub` → `$XDG_CACHE_HOME/huggingface/hub`, `~`/`$VAR` expanded) + `hf_weights_present()` (per backend: the `refs/main` snapshot holds every file that backend's `from_local` loads and no `blobs/*.incomplete` is left, so an interrupted prefetch is not "ready"), `opencode` on PATH, `Paths(out)` helper creating `raw/ audio/ clips/ final/ logs/` (`clips/` is reserved and stays empty). `smart_app_control(winreg=None)` reads `HKLM\SYSTEM\CurrentControlSet\Control\CI\Policy\VerifiedAndReputablePolicyState` via `winreg` on win32 (1 on, 2 evaluation, 0 off; missing/unreadable -> `unknown`; never raises; a fake module can be passed in) and `detect` adds `SAC_HINT` when it is on.
- `scenario.py` — load + validate scenario JSON (no third-party schema lib; hand-written validation with clear messages); resolve relative file paths against the scenario file's directory.
- `chrome.py` — launch Chrome with `--remote-debugging-port` (random free port), fresh `--user-data-dir` under `<out>/chrome-profile` (when a leftover profile cannot be removed, a unique `<out>/chrome-profile-<port>` is used and logged rather than reusing it), `--window-size`, `--window-position=0,0`, `--no-first-run`, `--disable-features=Translate`, optional `--headless=new`; connect with Playwright `connect_over_cdp`; return (browser, context, page, cdp_session, window_bounds); clean shutdown, which also removes the profile directory (it holds the app session's cookies and tokens under the agent-readable output tree); `env_omit` names environment variables Chrome must not inherit.
- `drive.py` — execute a scenario: login, then each step's `actions` then `expect`; smooth mouse moves (`page.mouse.move` with steps) so a cursor is visible; wait for real completion signals (selector visible / page text; there is no network-idle wait) with per-step timeout; screenshots; returns `StepResult` list and per-step wait windows; `run_steps(page, scenario, out, clock=None, pacer=None, screenshot_prefix="step", do_login=True)` is shared by `dryrun` and `record` (`record` logs in itself and passes `do_login=False`). `selector` expectations count visible elements only. `record` stops the capture on any error before closing Chrome. `login` only types credentials into a loopback host (see `.env` loading); `logs/failure-<id>.html` is written through `_failure_html`, which empties `input[type=password]` values in the page and scrubs them from the HTML.
- `cursor.py` — JS injected via `Page.addScriptToEvaluateOnNewDocument`: draws a cursor div that follows `mousemove` and pulses on `mousedown` (pointer-events none, z-index max) so screencast/headless recordings show the pointer.
- `capture.py` — two backends. `ScreencastCapture` (default): CDP `Page.startScreencast` (jpeg, q80, maxWidth/Height = viewport), acks every frame, writes `raw/frames/NNNNNN.jpg` + timestamps, and on stop assembles `raw/capture.mp4` with the ffmpeg concat demuxer (per-frame `duration` = time to the next kept frame, no minimum, so cumulative concat time equals real time; frames that arrive less than half a frame period after the previous kept one are dropped) at 30 fps CFR (`-vf fps=30 -c:v libx264 -crf 18 -pix_fmt yuv420p`). `ScreenCapture`: OS grabber of the page area of the Chrome window (window bounds + measured `ui_insets`, viewport-sized, multiplied by `device_scale_factor` for the grab and scaled back to the viewport) (Windows `gdigrab -i desktop -offset_x -offset_y -video_size`, macOS `avfoundation -i "Capture screen <idx>:none"` + crop, Linux `x11grab -i :0.0+x,y -video_size`), 30 fps, `-c:v libx264 -preset ultrafast -crf 18`, stopped by writing `q` to stdin. Both expose `start() -> t0`, `now() -> seconds since t0`, `stop() -> path`, `abort()` (error path, never raises) and record `t_stop` (capture length, used for `markers.capture_seconds`).
- `tts.py` — `synthesize(text, ref_wav, backend) -> (np.ndarray float32 mono, sr)`. Backends: `turbo` (`chatterbox.tts_turbo.ChatterboxTurboTTS.from_pretrained(device)`), `nano` (same with `nano=True`; only git builds of chatterbox-tts ship it, the PyPI releases ≤ 0.1.7 do not, so a build without it raises a clear `TTSError`), `classic` (`chatterbox.tts.ChatterboxTTS`, uses `exaggeration`/`cfg_weight`), `tone` (synthetic: a quiet 220 Hz tone + amplitude envelope, length = words/2.5 s, for tests/dry runs; needs no ML deps), `auto` (cuda/rocm/mps → turbo; otherwise nano when the installed chatterbox has it, else turbo). Loads the model once per process; the "run prefetch" hint is added only for Hugging Face cache/offline errors. Writes WAV via soundfile at the model's `sr` (24000). Stats helper: duration, peak/rms dBFS, `silent` (rms < -50 dBFS), `clipped` (>0.5% samples at |1.0|).
- `narration.py` — template generation, JSON validation (`{"intro": str, "outro": str, "steps": [{"id","text"}]}`, ids must equal scenario step ids in order, per-segment ≤ 45 words, total words ≤ max_length_seconds * 2.6), LLM request via an OpenAI-compatible `/v1/chat/completions` POST (urllib only, no SDK) with a strict JSON-only instruction, one repair retry, then template fallback.
- `pacing.py` — pure functions: given `durations.json` and step order, compute planned start offsets: intro plays from t=0 while holding on the first screen; `step[i].t_start = max(prev.t_end + 0.3, prev.t_start + durations[prev.id])`; outro starts at `max(last.t_end, last.t_start + durations[last.id])`; capture ends 2.0 s after outro end. `record` uses this live (waits before each step); `edit` reads the actual times from `markers.json`.
- `markers.py` — schema: `{"capture_start_epoch": float, "intro_t": 0.0, "outro_t": float, "end_t": float, "steps": [{"id","t_start","t_end","status","wait_windows": [[t0,t1],...]}]}`.
- `edit.py` — build the final timeline from `markers.json`: (1) speed up any `wait_window` longer than 1.5 s by 4x only if that step's narration has already finished before the window starts, otherwise leave it; (2) place `seg-intro.wav` at `intro_t`, each `seg-<id>.wav` at its (remapped) `t_start`, `seg-outro.wav` at `outro_t`; (3) crop to the app viewport if the raw capture is larger than the viewport (screen backend); (4) `loudnorm=I=-16:TP=-1.5:LRA=11` on the narration bus (narration is delayed with `adelay=D|D` per channel, which every ffmpeg 4.x accepts; ffmpeg >= 4.2 is the minimum); (5) mux H.264 `-crf 20 -pix_fmt yuv420p -movflags +faststart`, AAC 160k 48 kHz. Single ffmpeg filter_complex where possible; write the exact command to `logs/edit.json`.
- `verify.py` — with ffprobe (or the fallback parser): duration ≤ max_length_seconds + 10; |audio − video| ≤ 0.5 s; no black frame in the first and last 1.0 s (`blackdetect`); narration audible (`volumedetect` mean_volume > −30 dB); thumbnails at 10/50/90%. All results in `logs/verify.json` with pass/fail per check.
- `report.py` — `report.md` (verdict, step table, checks table, artifact paths, env summary) and `result.json`.
- `llm.py` — OpenAI-compatible helpers used by `check-model` and `narrate-llm` (urllib, timeouts, clear errors, never raises past the CLI boundary).
- `cli.py` + `__main__.py` — argparse subcommands mapping to the table above; `_load_dotenv(argv)` runs first, then `build_parser()` (core commands, then `onboard_audio.register` / `onboard_scenario.register`, then `bench.register` / `bench_meta.register` / `opencode_events.register`), then `args.fn(args)`. `_log_failure` skips `run` and `record-ref` (whose `--out` is a file, not a `Paths()` tree) and writes only `<out>/logs/<cmd>.json` for `bench` / `bench-meta` (a bench directory is not a `Paths()` tree).
- `dotenv.py` — minimal `.env` parser/writer (`NAME=value`, optional `export`, `#` comments, single/double quotes with `\n \t \" \\` unescaped) and credential resolver: `env_path`, `parse`, `read`, `names`, `format_value`, `write_value` (0600, in-place update), `is_op_ref`, `op_path`, `resolve_op` (`op read REF`, raises `OpError`), `lookup` (no `op`), `resolve` → `(value, "environ"|".env"|"op://"|"missing"|"op-error: ...")`, `load_env(env_file=None, resolve_refs=True)` → dict of names it set (`load_env.unresolved` holds the reasons; with `resolve_refs=False` `op://` names go to `load_env.deferred` instead), `credential(name)` → `(value, None)` or `(None, why)` resolving a deferred reference on first use, `forget_deferred()`.
- `onboard_audio.py` (+ `passage.txt`) — `record-ref` and `devices`: `passage_text/passage_chunks/print_passage`; pure DSP helpers `frame_rms_db`, `normalize_peak`, `trim_silence`, `clipped_pct`, `analyze`, `warnings_for`, `process(raw, sr) -> (audio, stats)` (normalise first, then trim at -40 dBFS; noise floor = 10th percentile of 50 ms frame RMS, speech frames = > 6 dB above it, `speech_seconds` drives the "too short" warning), `write_wav16`; device listing `list_input_devices()` (sounddevice imported lazily via importlib, so the module works without it), `list_screens(os_name, ffmpeg)`, `parse_dshow_devices`, `parse_avfoundation_devices`; recording `ffmpeg_record_args(...)` (pure argv builder), `ffmpeg_candidates(os_name, device, ffmpeg)`, `record_ffmpeg` (records to `<name>.raw.wav`, deletes it even when ffmpeg times out, resamples to 48 kHz if ffmpeg ignored `-ar`; ffmpeg output is decoded as UTF-8 so dshow names with `®` survive, and the first informative stderr line is reported), `record_sounddevice` (one retry at the device's default rate), `prepare_capture` (backend resolution + input priming before the countdown), `run_capture`, `record_ref(out, seconds, device, backend, show_countdown) -> sidecar dict`; `cmd_record_ref`, `cmd_devices`, `register(subparsers, run_map)`.
- `onboard_scenario.py` — `creds`, `init-scenario`, `validate`, `inspect`: `scaffold(...)`, `strip_todos(data)`, `validate_file(path)`, `inspect_url(...)`, `collect_elements`, `selector_candidates`, `choose_selector` (first unique candidate via `page.locator(sel).count()`), `classify`, `format_table`, the `cmd_*` handlers, `register(subparsers, run_map)` and `main(argv)` (standalone `python -m demo_smoke.onboard_scenario ...`). `inspect --login-from` calls `dotenv.load_env` itself and `drive.login`.
- `llm.py` also has `list_models(base_url, timeout=10) -> list[str]` (`GET /v1/models`, `data[].id`; raises `LLMError`), `LOCAL_ENDPOINTS` (ollama/lmstudio/llama.cpp roots) and `probe_local_endpoints(timeout=2.0, endpoints=None) -> list[dict]` (`{"name","base_url","reachable","models","error"}`, never raises) used by `env.detect` for the doctor report; `env.tts_advice(device, os_name=None)` is the one-line TTS recommendation.
- `bench.py` — the `bench` command: driver specs, one subprocess per driver run, `collect` (a run directory -> per-run `bench.json`), the agent verdict; see the Bench section.
- `bench_report.py` — `aggregate` (one row per driver, means over its PASS runs), `differences`, `DIR/report.md` + `DIR/bench.json`, baseline loading/validation; see the Bench section.
- `bench_meta.py` — `DisplayCapture` (whole-display recording), the spoken meta narration, the meta video and the `bench-meta` command; see the Bench section.
- `ffmpeg_concat.py` — `concat_videos` (clips of different sizes -> one H.264 file via the concat filter) and `encode_timeout`; see the Bench section.
- `opencode_events.py` — tolerant parser + `summary` for `opencode run --format json` streams and the `opencode-events` command; see the Bench section.

## Scenario JSON

```json
{
  "name": "Chat with Manuals",
  "slug": "chat-with-manuals",
  "app_url": "http://localhost:3000",
  "viewport": {"width": 1920, "height": 1080},
  "login": {"type": "none"},
  "max_length_seconds": 90,
  "intro": "One sentence spoken over the first screen.",
  "outro": "One sentence spoken at the end.",
  "steps": [
    {"id": "open", "title": "Open the app", "narration": "What I say during this step.",
     "actions": [{"goto": "/"}], "expect": [{"text": "Chat with Manuals"}], "timeout_s": 30},
    {"id": "upload", "title": "Upload manuals",
     "actions": [{"upload": {"selector": "input[type=file]", "files": ["fixtures/osha-1910.pdf"]}}],
     "expect": [{"selector": ".doc-chip", "count_min": 1}]},
    {"id": "ask", "title": "Ask a question",
     "actions": [{"fill": {"selector": "textarea", "text": "What is the ladder inspection interval?"}},
                 {"click": "button[type=submit]"}],
     "expect": [{"selector": ".answer", "contains": "inspect"}, {"text": "[1]"}]}
  ]
}
```

`login.type`: `none` | `form` (`url`, `username_selector`, `password_selector`,
`submit_selector`, `username_env`, `password_env`, optional `success_selector`)
| `basic` (`username_env`, `password_env`, sent as an `Authorization: Basic` header on requests to `app_url`'s origin only, never cross-origin).
Actions: `goto` (path or absolute URL), `click` (selector), `fill`
`{selector,text}`, `type` `{selector,text,delay_ms}`, `press` (key),
`upload` `{selector, files[]}`, `hover` (selector), `scroll` `{selector}` or
`{y}`, `wait` `{ms}`, `wait_for` `{selector|text, timeout_s}`, `screenshot`
(name). Expect: `text` (visible page text contains), `selector` (+ optional
`contains`, `count_min`), `url_contains`, `not_text`. Relative file paths are
resolved against the scenario file's directory. Step `timeout_s` default 60.
Unknown keys are rejected everywhere, mirroring `scenarios/schema.json` (top level,
`viewport`, `login` per type, the object-valued actions `fill`/`type`/`upload`/`wait`/`wait_for`
and every `expect`); the only top-level exceptions are `$schema` (editor hint) and `_dir`/`_path`
(added by `load()`). `count_min` must be an integer; `login.url` must be non-empty.
`selector` counts visible elements only.

## Timing model

`record` runs the scenario once more with pacing: the capture starts, the
intro segment "plays" (the page is held on the first screen for its
duration), then each step waits until the previous step's narration would
have finished before acting, so on-screen actions land while the matching
sentence is being spoken. Actual times are written to `markers.json`;
`edit` positions the audio from those actual times and applies the
optional wait-window speed-ups.

## Test strategy (all runnable here, no GPU, no display)

- Unit: scenario validation errors, pacing math, edit timeline computation (pure function returns the list of segments + audio offsets), narration validation + template, tts `tone` backend + stats, markers I/O, verify parsing on synthetic media (`lavfi testsrc`/`sine`).
- Browser: headless Chromium (whatever `DEMO_SMOKE_CHROME` or Chrome discovery finds; skipped when none) over CDP against `tests/fixtures/app` served by `http.server` on a random port: login form, file upload chips, ask → delayed answer with citation. Cover a PASS scenario and a FAIL scenario (wrong expectation) with exit code 2 and a failure report containing console errors.
- E2E (`tests/test_e2e_run.py`): `run` with `--tts tone --capture screencast --narration template --headless` against the fixture app → `final/*.mp4` exists, verify passes, `result.json.verdict == "PASS"`.
- Onboarding: `tests/fakes/sounddevice.py` (synthetic speech bursts + silence, `config` knobs for noise/level/length/failure) injected into `sys.modules` → `record-ref` trims/normalises, stats and warnings; `devices` per OS from canned ffmpeg listings; `creds set --value-from-stdin` → 0600 `.env`, `list` names only, `check` via a fake `op` script (`tests/fakes/op`, `op.cmd`) on PATH; `init-scenario` scaffold validates after `strip_todos`; `inspect` against the fixture app finds `#question`, `#ask-btn`, `#file-input`; `check-model --list` and doctor's `local_endpoints` against the conftest `FakeLLM`; `cli.main` `.env` loading (environment wins, `--env-file` first, `--version` skips it).
- OpenCode: `tests/test_permissions_match.py` re-implements OpenCode's `Permission.evaluate` (last matching rule wins over defaults → `opencode.json` → agent frontmatter) and asserts every command the playbooks run is allowed and `creds set`/`prefetch`/`rm -rf`/`git push` are denied; `tests/test_opencode_e2e.py` runs the REAL `opencode run --agent demo-smoke --auto` under a scratch `HOME` against `tests/opencode_fake_llm.py` (a scripted OpenAI-compatible server answering JSON or SSE with the next kit command as a `bash` tool call) through doctor → dryrun → ... → verify (skipped without `OPENCODE_BIN`/Chrome).
- Lint: `ruff check .` clean with default rules.

Test tooling: `requirements.txt` + `requirements-dev.txt` (pytest, ruff) in a
venv, plus a Chrome/Chromium binary. Never `pip install` torch/chatterbox for
the tests; the ML backends are exercised by unit tests only through a mocked
import.

## Function contract (builders must match these exactly; review round 2 added the
## keyword parameters the CLI already relied on: `check_files`, `online`, `device`,
## `tail`, `measure_audio`)

```python
# demo_smoke/env.py
def detect(base_url: str | None = None, model: str | None = None, timeout: int | None = None) -> dict   # doctor report; timeout -> tool-call probe
def find_ffmpeg() -> str                      # raises RuntimeError with install hint if none
def find_ffprobe() -> str | None
def find_chrome() -> str | None
def torch_device() -> str                     # "cuda" | "rocm" | "mps" | "cpu" | "none" (torch missing)
def chatterbox_nano_supported() -> bool | None   # None when chatterbox is not importable
def hf_cache_dir() -> str ; def hf_weights_present(cache: str | None = None) -> dict   # {"turbo","nano","classic": bool}
class Paths:                                  # Paths(out).raw/.audio/.clips/.final/.logs (Path), mkdirs on init
def media_info(path, measure_audio: bool = True) -> dict   # {"duration": float, "width": int, "height": int, "has_audio": bool, "audio_duration": float|None} via ffprobe or ffmpeg -i parsing (measure_audio: decode the audio for an exact audio_duration in the ffmpeg -i fallback)

# demo_smoke/scenario.py
def load(path: str | Path, check_files: bool = False) -> dict   # validated; adds "_dir" (Path) and resolves file paths; raises ScenarioError(msg); check_files=True also requires upload files to exist
def validate(data: dict) -> list[str]         # error messages, [] if valid
class ScenarioError(ValueError)

# demo_smoke/chrome.py
class ChromeSession:                          # context manager
    page; context; browser; cdp               # cdp = page.context.new_cdp_session(page)
    window_bounds: dict                       # {"x","y","width","height"} from Browser.getWindowBounds (DIPs)
    ui_insets: dict                           # {"x","y"} browser UI between window origin and page area (DIPs)
    device_scale_factor: float                # physical pixels per DIP (window.devicePixelRatio)
    def close(self) -> None
def launch(out: Path, viewport: dict, headless: bool = False) -> ChromeSession

# demo_smoke/cursor.py
CURSOR_JS: str                                # injected on every new document
def install(cdp) -> None

# demo_smoke/drive.py
def dryrun(scenario: dict, out: Path, headless: bool = False) -> dict
#   -> {"verdict": "PASS"|"FAIL", "steps": [ {"id","title","status":"PASS"|"FAIL"|"SKIPPED","expected","observed","screenshot","seconds","error"} ],
#       "console_errors": [str], "failed_requests": [{"url","status","body_excerpt"}], "attempts": int}
#   writes logs/step-NN-<id>.png, logs/smoke-results.md, logs/dryrun.json ; retries the whole scenario once on failure
def record(scenario: dict, out: Path, capture: str, headless: bool, durations: dict) -> dict
#   -> markers dict (see markers.py); writes raw/capture.mp4 and logs/markers.json
def run_steps(page, scenario: dict, out: Path, clock=None, pacer=None, screenshot_prefix: str = "step",
              do_login: bool = True) -> list[dict]
#   shared executor; clock() -> seconds since capture start (or None); pacer(step_index, step_id) blocks until this step may start
#   do_login=False when the caller already logged in (record does, so it can hold the first screen during the intro)

# demo_smoke/capture.py
class ScreencastCapture:  def __init__(self, session: ChromeSession, out: Path, fps: int = 30); start(); now() -> float; stop() -> Path; abort()
class ScreenCapture:      def __init__(self, session: ChromeSession, out: Path, fps: int = 30); start(); now() -> float; stop() -> Path; abort()
def grab_args(ffmpeg, os_name, bounds, fps, out_path, screen_index: int = 0, display=None, scale: float = 1.0) -> list[str]
def page_bounds(session) -> dict              # window_bounds + ui_insets, viewport-sized (DIPs)
def make(kind: str, session, out: Path) -> ScreencastCapture | ScreenCapture

# demo_smoke/tts.py
def synthesize(text: str, ref_wav: Path | None, backend: str = "auto", device: str | None = None,
               exaggeration: float = 0.5, cfg_weight: float = 0.5, online: bool = False) -> tuple["np.ndarray", int]
def synth_all(out: Path, ref_wav: Path | None, backend: str = "auto", online: bool = False,
              device: str | None = None) -> dict      # reads audio/narration.json, writes seg-*.wav + audio/durations.json, returns durations
def set_offline_env(online: bool = False) -> None    # exports HF_HUB_OFFLINE=1 / TRANSFORMERS_OFFLINE=1 (setdefault) unless online
def audio_stats(wav, sr: int) -> dict         # {"duration","peak_dbfs","rms_dbfs","silent","clipped"}
def resolve_backend(backend: str) -> str      # "auto" -> "turbo" on cuda/rocm/mps; else "nano" if chatterbox_nano_supported() else "turbo"

# demo_smoke/narration.py
def template(scenario: dict) -> dict
def validate(narr: dict, scenario: dict) -> list[str]
def from_llm(scenario: dict, base_url: str, model: str, timeout: int = 180) -> tuple[dict, str, str]   # (narration, source "llm"|"template", note)
def words(text: str) -> int

# demo_smoke/pacing.py
def next_start(prev_t_start: float, prev_t_end: float, prev_duration: float, gap: float = 0.3) -> float
def plan(step_ids: list[str], durations: dict, step_seconds: dict | None = None, gap: float = 0.3, tail: float = 2.0) -> dict
#   -> {"intro_t": 0.0, "steps": {id: t_start}, "outro_t": float, "end_t": float}   (step_seconds: estimated step run time from dryrun, default 3.0)

# demo_smoke/markers.py
def new(capture_start_epoch: float) -> dict
def add_step(m: dict, step_id: str, t_start: float, t_end: float, status: str, wait_windows: list) -> None
def save(m: dict, out: Path) -> Path ; def load(out: Path) -> dict

# demo_smoke/edit.py
def plan_timeline(markers: dict, durations: dict, min_wait: float = 1.5, speed: float = 4.0, tail: float = 1.0) -> dict
#   pure -> {"video_segments": [{"src_start","src_end","speed"}], "audio": [{"id","t"}], "total": float, "map": callable-free description}
def build(out: Path, scenario: dict) -> Path  # final/<slug>.mp4 ; writes logs/edit.json with the ffmpeg command(s)

# demo_smoke/verify.py
def check(out: Path, scenario: dict) -> dict  # {"pass": bool, "checks": [{"name","pass","detail"}], "duration": float, "thumbnails": [str]} ; writes logs/verify.json

# demo_smoke/report.py
def write(out: Path, scenario: dict, dryrun: dict | None, markers: dict | None, verify: dict | None,
          env: dict, narration_source: str, verdict: str, error: str | None = None) -> tuple[Path, Path]

# demo_smoke/llm.py
def chat(base_url: str, model: str, messages: list, tools: list | None = None, timeout: int = 120,
         temperature: float = 0.1, response_json: bool = False) -> dict          # raw response; raises LLMError(msg)
def probe_tool_call(base_url: str, model: str, timeout: int = 120) -> dict        # {"pass": bool, "detail": str}
def reachable(base_url: str, timeout: int = 5) -> bool                            # GET {base_url}/models
def opener_for(url: str) -> urllib.request.OpenerDirector                         # proxy-free for loopback hosts
class LLMError(RuntimeError)

# demo_smoke/cli.py
def main(argv: list[str] | None = None) -> int       # exit code; __main__ calls sys.exit(main()); loads .env first
def build_parser() -> argparse.ArgumentParser        # core commands + onboard_audio.register + onboard_scenario.register

# demo_smoke/llm.py (onboarding additions)
def list_models(base_url: str, timeout: int = 10) -> list[str]                    # GET /v1/models ids; raises LLMError
def probe_local_endpoints(timeout: float = 2.0, endpoints=None) -> list[dict]     # [{"name","base_url","reachable","models","error"}]

# demo_smoke/env.py (onboarding additions)
def tts_advice(device: str, os_name: str | None = None) -> str                    # doctor "tts:" line
def smart_app_control(winreg=None) -> str | None                                 # "on"|"evaluation"|"off"|"unknown"; None off Windows (bench pass)

# demo_smoke/dotenv.py
def load_env(env_file: str | Path | None = None, resolve_refs: bool = True) -> dict[str, str]   # exports unset names; load_env.unresolved / .deferred
def credential(name: str) -> tuple[str | None, str | None]                                       # environ, else a deferred op:// resolved now (not exported)
def forget_deferred() -> None
def resolve(name: str, env_file=None) -> tuple[str | None, str]                   # environ -> .env -> op://
def write_value(env_file, name: str, value: str) -> Path                          # 0600, in-place update

# demo_smoke/onboard_audio.py
def process(raw: "np.ndarray", sr: int) -> tuple["np.ndarray", dict]              # normalise + trim + stats
def record_ref(out: Path, seconds: float = 60, device: str | None = None, backend: str = "auto",
               show_countdown: bool = True) -> dict                               # sidecar dict (exit_code inside)
def register(subparsers, run_map: dict) -> None                                   # record-ref, devices

# demo_smoke/onboard_scenario.py
def scaffold(name: str, url: str, steps: list[tuple[str, str]], login: str = "none", ...) -> dict   # (title, todo) pairs
def strip_todos(data: dict) -> list[str]                                          # removes step "todo" keys, returns warnings
def register(subparsers, run_map: dict) -> None                                   # creds, init-scenario, validate, inspect
def main(argv: list[str] | None = None) -> int
```

## Bench: "smoke test the smoke test" (meta review; added after the onboarding widget)

Run the SAME scenario under several drivers, collect hard numbers per run, optionally record
the run itself as a meta demo, and write one side-by-side report so local models can be
compared with hosted models and with the manual Codex/Claude runs. Modules
`demo_smoke/bench.py`, `demo_smoke/bench_report.py`, `demo_smoke/bench_meta.py`,
`demo_smoke/ffmpeg_concat.py`, `demo_smoke/opencode_events.py`; they register on the main
parser like the onboarding commands (`register(subparsers, run_map)`), return exit codes and
follow the table above. `bench` runs OpenCode itself, so there is no new slash command.

| cmd | args | output |
|---|---|---|
| `bench` | `SCENARIO --out DIR [--driver SPEC ...] [--tts ...] [--ref REF.wav] [--headless] [--repeat N] [--record-screen] [--meta-narrate] [--baseline bench/baseline.json] [--opencode-bin PATH] [--timeout-s N] [--llm-timeout N]` (`--driver` repeatable, default `template`; the `llm:<url>\|<model>` spec must be quoted, `\|` is a shell pipe; `--tts` reaches the template/llm drivers as `run --tts` and an OpenCode agent as a `tts:<backend>` token in its smoke command, whose playbook still switches to `tone` when doctor finds no usable TTS; `--timeout-s` default 3600 and kills the whole driver process tree; `--llm-timeout` default 180 = the llm driver's `run --timeout`; hidden `--meta-from-clips a.mp4 b.mp4` for tests) | `DIR/runs/<driver-slug>/r<N>/` = a normal kit output dir per run (`report.md`, `result.json`, `logs/`, `final/`) plus `bench.json` (metrics), `logs/bench-stdout.txt` / `bench-stderr.txt` (the driver process) and, for the agent path, `logs/opencode-events.json`; `DIR/report.md` (one table: driver, model, verdict, total minutes, narration source, tool calls, narration words, on-screen reference fraction, validation retries, video seconds, tokens/cost, notes; `manual` rows from `--baseline`; "what differed"; links; per-run appendix), `DIR/bench.json` (+ the same file as `DIR/logs/bench.json`), `DIR/meta/clips/NN-<slug>-rN.mp4` with `--record-screen` (every driver kind, one clip per run), `DIR/meta/<slug>-bench.mp4` with `--meta-narrate`. Exit 0 all runs PASS, 2 any FAIL, 3 any ERROR (or no runs), 4 bad input (driver spec, scenario, `--repeat` < 1, a `--ref` path that does not exist, unreadable `--baseline`, `--meta-narrate` without `--record-screen`/`--meta-from-clips`, and argparse usage errors), 130 on Ctrl-C (report still written). `--out` is resolved to an absolute path (driver subprocesses run with `cwd=<kit>`) |
| `bench-meta` | `[--out DIR (default demo-output/bench)] [--bench-json PATH] [--clips MP4 ...] [--baseline PATH] [--tts ...] [--ref REF] [--online] [--gap S] [--align-clips]` | rebuilds only the meta video for an existing bench directory: `DIR/meta/narration.json`, `meta/audio/seg-*.wav` + `durations.json`, `meta/concat.mp4`, `meta/meta-filter.txt`, `meta/meta-edit.json` (plan + exact argv), `meta/<slug>-bench.mp4`; `logs/bench-meta.json`; default clips `DIR/meta/clips/*.mp4` in name order; `--align-clips` matches clips to driver segments by the `<slug>` in `NN-<slug>-rN.mp4` (by position only when no name carries a slug and there is one clip per driver; otherwise a note is logged and kept as `align_note` in `meta-edit.json`); every baseline entry is spoken; the template driver is never the spoken "fastest driver" (it runs no model); the concat and the final encode get a timeout of `max(900, 4 x footage seconds)` and `-preset veryfast`; a real `DIR/bench.json` is read through `bench.meta_view` (its `drivers` key is the spec list, never rows) and `--baseline` through `bench_report.load_baseline` (list or `{"entries": [...]}`, default: the entries stored in bench.json); exit 4 (no bench.json / no clips / a `--baseline` that is not a list of entries), 3 (tts, ffmpeg) |
| `opencode-events` | `EVENTS.jsonl [--out DIR] [--json]` | summarises a saved `opencode run --format json` stream (bench keeps each agent run's as `runs/<slug>/rN/logs/bench-stdout.txt`; its `logs/opencode-events.json` is the parsed summary) - tool calls, kit commands (every command of a chained bash call), steps, tokens, final status, unknown event types; `cost` is `None` when OpenCode priced the model at 0 while counting tokens (no catalog price, e.g. every `@<base-url>` override); `<out>/logs/opencode-events.json = {file, summary, events}` when `--out` is given; exit 4 when the file is missing |

Drivers: `template` (no LLM: the pipeline's own baseline), `llm:<base-url>|<model>`
(narration via an OpenAI-compatible endpoint, pipeline via a `run` subprocess),
`opencode:<provider/model>` (the full agent path: `opencode run --agent demo-smoke --auto
--model <provider/model> --format json --command smoke "<scenario> <run-out> [headless] [<ref>]"`
with `cwd=<kit>`, every path in the message double-quoted (OpenCode splits `$1 $2 ...` on
whitespace unless quoted, so a `C:\Users\First Last\...` path would otherwise land in two
placeholders), `OPENCODE_DISABLE_MODELS_FETCH=1`, `OPENCODE_DISABLE_AUTOUPDATE=1`,
`OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS=<timeout*1000>`, `NO_PROXY` for loopback,
`PYTHONIOENCODING=utf-8` and `DEMO_SMOKE_CHROME` pinned; `HOME` untouched so a hosted provider
uses the user's own OpenCode auth; `OPENCODE_CONFIG_CONTENT` always carries at least top-level
`model`/`small_model` = the driver's model so OpenCode's title/summary agents never fall back to
the kit's default local ollama model) and `opencode:<provider/model>@<base-url>` (same, but that
provider's `baseURL` and model id are overridden through `OPENCODE_CONFIG_CONTENT` too; a provider
the kit's `opencode.json` defines keeps its SDK, any other is defined as `@ai-sdk/openai-compatible`).
The binary is found via `--opencode-bin` -> `OPENCODE_BIN` -> `opencode` on PATH ->
`~/.opencode-bin/node_modules/.bin/opencode` (an npm-prefix install); a missing binary yields an
ERROR row, not a crash. Every driver process gets the bench interpreter's directory first on `PATH`
(so the agent's bare `python -m demo_smoke` is the venv running the bench), the kit on `PYTHONPATH`,
`DEMO_SMOKE_CHROME` and `NO_PROXY` for loopback. Every driver runs in its own process
group (`start_new_session` / `CREATE_NEW_PROCESS_GROUP`); on timeout or Ctrl-C the whole tree is
killed (`killpg` / `taskkill /T /F`) before the partial output is drained, and stdout/stderr are
decoded as UTF-8 regardless of locale.

Collected per run (`bench.json`): driver, model, started/finished ISO timestamps (`time.time()`),
`wall_s` total and per stage (doctor, dryrun, narrate, synth, record, edit, verify; from
`logs/run.json`, or for the agent path from the tool-call event timestamps, retries summed,
narrate = narrate-template + narrate-llm + narrate-validate), verdict (PASS/FAIL/ERROR), exit
code, failing stage/step; narration (source llm|template|agent, validation errors, retries, words
per segment, total words, estimated speaking seconds, `references_on_screen` = fraction of
segments containing a token >= 3 chars, not a stopword, from the scenario's `expect`
text/contains strings or step titles); audio (total and per-segment seconds); video (final
duration, verify checks); `opencode` block (tool_calls count + command list, kit calls,
assistant messages, permission prompts, denied calls, `steps` used vs `steps_limit` from
`.opencode/agents/demo-smoke.md`, tokens in/out/total and cost when the events carry them,
`narration_written_by_agent` - only a write/edit/redirect that *succeeded* counts, a denied write
does not, `used_narrate_template`); `llm` block (`attempts`, `problems`, `fallback`,
`fallback_reason`, read from the structured `llm` block `run` writes into `logs/run.json`; the
`[narrate]` stdout note is kept verbatim, nothing is parsed out of it); environment snapshot
(`logs/doctor.json`). The agent path has no `result.json`, so its verdict is derived: PASS when
`logs/verify.json` passes and dryrun PASSed - even when the session also reported an `error`
event (a provider error from title generation does not undo a delivered video; the message is
kept in `error` and the report notes "PASS with an error reported") - except a run the bench had
to kill on its timeout, which is ERROR whatever verify says (its wall time is the timeout, not a
measurement; the template/llm drivers are treated the same way); FAIL on dryrun FAIL, a kit
command exiting 2, or a failed verify; ERROR otherwise (non-zero opencode exit, error events,
timeout, step limit) with `failing_stage` = last kit command run.

Report honesty rules (`bench_report`): "tool calls" counts every OpenCode tool call and the
appendix adds "kit calls" (`python -m demo_smoke` commands only); the playbook-minimum sentence
compares kit calls with `PLAYBOOK_MIN_KIT_COMMANDS` (7), never the all-tools count; `total min`
(`total_minutes`) is the mean over a driver's PASS runs only (`pass_minutes`; `all_runs_minutes`
and `run_minutes` are kept alongside and the notes list every run's minutes under `--repeat`; a
row with no passing run shows the mean over every run instead and `pass_minutes` is null, so a
FAIL/ERROR row's minutes include timed-out runs), so a timed-out repeat never inflates a
`PASS k/n` row; every other mean (tool calls, kit calls, words, refs, video seconds, tokens, cost,
stages) is taken over the same PASS runs (`mean_over` = `PASS`, or `all` when none passed), carries
a `counts` entry and the table shows `(k/n)` when it came from k of the row's n runs;
"fastest"/"slowest" and the manual-baseline comparison use `pass_minutes` of the *model* drivers
only and say "no driver passed" / "no model driver passed" otherwise - the template driver runs no
model, so it is quoted apart as the pipeline-only baseline (the spoken meta outro does the same);
the tool-call figure in the playbook sentence is the per-run mean, never a total; `PASS k/n` for
mixed repeats, with the breakdown (`PASS 0/2 (FAIL 1, ERROR 1)`) when both kinds occurred; the
on-screen-reference sentence quotes model-authored narration only (the template scores by
construction) and the legend says it is a sanity check; a PASS run whose session reported an
`error` event is noted as such; an agent run that ran `narrate-template` is described as having
used the playbook fallback, not as "fell back"; template rows carry a legend that PASS says
nothing about narration quality; tokens/cost cells are `-` when absent; baseline rows are flagged
manual and their minutes are shown as given (their optional numeric keys are validated by
`load_baseline` in the bench's units - `references_on_screen` a fraction 0..1, seconds / counts /
minutes / USD non-negative - so a hand-written `80` never renders as `8000%`); tokens/cost cells
are `-` when absent, and the cost (OpenCode's catalog estimate) is dropped when OpenCode priced
the model at 0 while counting tokens. `bench/baseline.example.json` holds the manual
codex/claude entries (`--baseline` accepts a JSON list or `{"entries": [...]}` for both `bench`
and `bench-meta`); `.gitignore` hides everything else under `bench/` (runs, meta, report) except
`baseline.example.json` / `baseline.json` - a hand-written baseline is tracked on purpose.

OpenCode JSON events (`opencode_events.py`, learned from a real run of OpenCode 1.18.26 saved as
`tests/fixtures/opencode-events.sample.jsonl`, scrubbed: absolute paths -> `<tmp>`/`<kit>`/
`<python>`/`<home>`, ids -> per-prefix counters; refresh with `tests/opencode_capture_events.py`
after an upgrade): one object per line, `{type, timestamp (epoch ms), sessionID, part}` with
types `step_start`, `tool_use`, `step_finish`, `text`, `reasoning`, `error`. `parse(lines)` never
raises (empty/bytes/Path/iterable input, non-JSON or truncated lines, arrays on one line,
missing/odd fields) and lists unknown types once each; it extracts tool calls (name, command,
started/finished ms, `seconds`, status, exit code when present, kit command), assistant text,
permission lines (OpenCode prints `permission requested: ... auto-rejecting` as plain text, never
as JSON; with `--auto` nothing is printed), denied calls (error-status tool calls with a
rejection message), steps used + `step_limit_reached` (the "maximum number of steps" sentence in a
text/error event), usage summed over `step_finish` events, session id and `final_status`
(`empty | error | step_limit | completed | incomplete`). `summary(parsed)` is what the bench stores.

Meta recording (`bench_meta.py`): `DisplayCapture(out, fps=15)` records the whole display
(Windows `gdigrab -i desktop`, macOS `avfoundation -capture_cursor 1 -i "Capture screen N:none"`,
Linux `x11grab -i $DISPLAY.N`; even-size scale filter, libx264 ultrafast; `start()` raises
`capture.CaptureError` when ffmpeg dies within 0.5 s, `stop()` writes `q` and waits 10 s before
terminate/kill, `abort()` for the error path) around each opencode driver run; `build_argv` is the
pure argv builder. `meta_narration(bench_json, baseline=None)` takes the `bench.meta_view` shape
(`rows` + `scenario {name, slug}`; `bench-meta` applies the view to a real `DIR/bench.json` itself,
and `bench_rows` never mistakes the `drivers` spec list for rows) and writes a narration in the
kit's `narration.json` shape (intro "This is the smoke kit running itself under ...", one segment
per driver with minutes, tool calls, verdict and narration source spoken naturally - an aggregate
row with `runs` = N > 1 is spoken as "Averaged over N runs ... a passing run took ...", its
`verdicts` as "passed two of three runs" and its `narration_sources` as "the agent itself on two
runs and the scenario template on one run"; the outro's fastest passing driver uses
`pass_minutes`, so a `PASS 2/3` row qualifies); `build_meta_video(clips, narration, out, tts, ref, online)` synthesises with
`demo_smoke.tts`, concatenates the clips (`ffmpeg_concat.concat_videos(paths, out, fps=30,
size=None)`: concat *filter* with scale/pad/fps/setsar per input, audio dropped), places the
segments intro-first with a 0.5 s gap (last frame held with `tpad` when the narration is longer)
and mixes with `edit.audio_chain` (loudnorm), H.264 crf 20 + AAC. Segments cannot be recorded in
this container (no display); the argv builders and the start/stop plumbing are tested with
`tests/fakes/ffmpeg_fake.py`, the video path with lavfi `testsrc` clips.

Tests: `tests/test_bench_collect.py` (metrics from synthetic run dirs, driver parsing, report
wording, `bench_report.validate`), `tests/test_opencode_events.py` (the saved sample +
tolerance), `tests/test_bench_e2e.py` (`bench --driver template --driver
opencode:fake/scripted@<fake-url> --tts tone --headless --baseline bench/baseline.example.json`
against the fixture app with the REAL OpenCode binary and the scripted fake model: both rows
PASS, opencode `tool_calls >= 8`, `report.md` table present, `bench.json` validates; skipped only
when the binary or a Chrome is missing), `tests/test_bench_meta.py` (argv per OS, fake-ffmpeg
plumbing, spoken numbers, `--meta-from-clips` with two lavfi clips -> meta MP4 with audio).

Windows Smart App Control (same pass): a tablet showed the toast "Smart App Control has blocked
part of this app ... timezones.cp311-win_amd64.pyd" during setup; SAC blocks unsigned/unknown
binaries with no allowlist, so torch/pandas/librosa cannot import. `env.smart_app_control()`
reads the registry value on win32 (`doctor` reports `smart_app_control=` and `PROBLEMS
smart_app_control ON` + `SAC_HINT`), `scripts/bootstrap-tablet.ps1` reads the same value with
`Get-ItemProperty` in its preflight and warns in yellow (continues, never changes the setting),
README "Troubleshooting" has the toast text. Unit-tested with a fake `winreg` module.

```python
# demo_smoke/bench.py
def parse_driver(spec: str) -> Driver ; def parse_drivers(specs: list[str]) -> list[Driver]   # raises BenchError
def run_dir(out: Path, driver: Driver | str, n: int) -> Path                       # DIR/runs/<slug>/r<N>
def references_on_screen(narr: dict | None, scenario: dict) -> float | None
def collect(run_out: Path, driver: Driver, scenario: dict, n: int = 1, ...) -> dict   # one run's bench.json
def run_driver(driver: Driver, n: int, scenario_path: Path, scenario: dict, out: Path, *, tts, headless, ref, timeout, opencode_bin) -> dict
def find_opencode(explicit: str | None = None) -> str | None
def register(subparsers, run_map: dict) -> None ; def main(argv: list[str] | None = None) -> int

# demo_smoke/bench_report.py
def load_baseline(path) -> list[dict] ; def baseline_rows(entries: list[dict]) -> list[dict]
def aggregate(runs: list[dict]) -> list[dict] ; def differences(rows, runs, baseline=None) -> list[str]
def build_markdown(out: Path, bench: dict) -> str ; def write(out: Path, bench: dict) -> tuple[Path, Path]
def validate(bench: dict) -> list[str]                                              # [] when bench.json is well-formed

# demo_smoke/opencode_events.py
def parse(lines) -> dict ; def parse_file(path) -> dict ; def summary(parsed: dict) -> dict
def stage_seconds(tool_calls: list[dict]) -> dict ; def wrote_narration(tool_calls: list[dict]) -> bool
def kit_command_of(command: str | None) -> str | None
def register(subparsers, run_map: dict) -> None

# demo_smoke/bench_meta.py
def build_argv(os_name: str, out_path, fps: int = 15, display_index: int = 0, display: str | None = None) -> list[str]   # without the ffmpeg executable
class DisplayCapture:                          # DisplayCapture(out, fps=15).start() / stop() -> Path / abort()
def meta_clip_path(bench_dir, index: int, driver_slug: str, repeat: int = 1) -> Path   # DIR/meta/clips/NN-<slug>-rN.mp4
def meta_narration(bench_json, baseline=None) -> dict                                # narration.json shape
def build_meta_video(clips: list[Path], narration: dict, out: Path, tts: str = "tone", ref=None, online: bool = False, ...) -> Path
def meta_output_path(bench_dir: Path, bench_json) -> Path                            # DIR/meta/<slug>-bench.mp4
def register(subparsers, run_map: dict) -> None                                      # bench-meta

# demo_smoke/ffmpeg_concat.py
def concat_videos(paths: list[str | Path], out: str | Path, fps: int = 30, size: tuple[int, int] | None = None) -> Path
```
