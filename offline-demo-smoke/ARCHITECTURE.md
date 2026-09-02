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
  requirements.txt           playwright, numpy, soundfile, imageio-ffmpeg
  requirements-tts.txt       chatterbox-tts (+ torch/torchaudio, see README for index URLs)
  scripts/setup.sh, scripts/setup.ps1
  scenarios/schema.json, scenarios/example-chat-with-manuals.json
  demo_smoke/                python package (see modules)
  tests/                     pytest; tests/fixtures/app/ is a static mock app
  demo-output/               default OUTPUT_DIR (gitignored)
```

## CLI contract  (`python -m demo_smoke <cmd> ...`)

Every command prints a one-line human summary to stdout, writes JSON to
`<out>/logs/<cmd>.json`, and uses exit codes: 0 ok, 2 feature failed
(smoke test FAIL), 3 pipeline/tooling error, 4 bad input.

| cmd | args | output |
|---|---|---|
| `doctor` | `[--base-url URL --model NAME]` | env report: os, python, ffmpeg path+version, chrome path, torch device (cuda/rocm/mps/cpu/none), chatterbox importable, ollama/OpenAI-compatible endpoint reachable, optional tool-call probe |
| `check-model` | `--base-url URL --model NAME` | sends a chat completion with one tool (`get_step_status`) and checks the model returns a tool call; PASS/FAIL |
| `prefetch` | `--tts turbo\|nano\|classic` | downloads Chatterbox weights into the HF cache (online step); prints cache dir |
| `voice-check` | `--ref REF.wav --out DIR --tts auto\|turbo\|nano\|classic\|tone` | `audio/voice_check.wav` + stats (duration, peak dBFS, rms dBFS, silent, clipped) |
| `dryrun` | `SCENARIO --out DIR [--headless]` | drives steps, `logs/step-NN-<id>.png`, `logs/smoke-results.md`, `logs/dryrun.json`; exit 2 on FAIL |
| `narrate-template` | `SCENARIO --out DIR` | `audio/narration.json` from scenario `intro`/`outro`/step `narration` fields |
| `narrate-llm` | `SCENARIO --out DIR --base-url URL --model NAME` | asks the local model for narration JSON, validates, falls back to template on any failure (and says so) |
| `narrate-validate` | `--out DIR [--max-seconds N]` | validates `audio/narration.json` (ids match scenario, word budget); exit 4 on invalid |
| `synth` | `--out DIR --ref REF.wav --tts ...` | `audio/seg-intro.wav`, `audio/seg-<id>.wav`, `audio/seg-outro.wav`, `audio/durations.json` |
| `record` | `SCENARIO --out DIR --capture screencast\|screen [--headless]` | paced run: `raw/capture.mp4`, `logs/markers.json` |
| `edit` | `--out DIR` | `final/<slug>.mp4` |
| `verify` | `--out DIR` | `logs/verify.json`, `final/thumb-{10,50,90}.png`; exit 2 on failed checks |
| `run` | `SCENARIO --out DIR [--tts ...] [--capture ...] [--narration template\|llm] [--ref REF] [--headless] [--base-url --model]` | whole pipeline in order: doctor → dryrun → narrate → synth → record → edit → verify → report; `report.md`, `result.json` |

Env overrides: `DEMO_SMOKE_CHROME` (chrome binary), `DEMO_SMOKE_FFMPEG`
(ffmpeg binary), `DEMO_SMOKE_FFPROBE`, `HF_HOME`. Offline: the kit sets
`HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` before importing chatterbox
unless `--online` is passed.

ffmpeg discovery order: `DEMO_SMOKE_FFMPEG` → `ffmpeg` on PATH →
`imageio_ffmpeg.get_ffmpeg_exe()`. ffprobe: `DEMO_SMOKE_FFPROBE` → PATH →
if absent, use `ffmpeg -i` parsing via a small helper (imageio-ffmpeg ships
no ffprobe). Chrome discovery: env → common per-OS install paths → PATH
names (`google-chrome`, `chrome`, `chromium`, `chromium-browser`) →
`/opt/pw-browsers/chromium-*/chrome-linux/chrome`.

## Modules (package `demo_smoke`)

- `env.py` — OS/ffmpeg/chrome/torch-device discovery, `Paths(out)` helper creating `raw/ audio/ clips/ final/ logs/`.
- `scenario.py` — load + validate scenario JSON (no third-party schema lib; hand-written validation with clear messages); resolve relative file paths against the scenario file's directory.
- `chrome.py` — launch Chrome with `--remote-debugging-port` (random free port), fresh `--user-data-dir` under `<out>/chrome-profile`, `--window-size`, `--window-position=0,0`, `--no-first-run`, `--disable-features=Translate`, optional `--headless=new`; connect with Playwright `connect_over_cdp`; return (browser, context, page, cdp_session, window_bounds); clean shutdown.
- `drive.py` — execute a scenario: login, then each step's `actions` then `expect`; smooth mouse moves (`page.mouse.move` with steps) so a cursor is visible; wait for real completion signals (selector/text/network idle) with per-step timeout; screenshots; returns `StepResult` list and per-step wait windows; `run_steps(page, scenario, out, pacing=None, on_step_start=None)` is shared by `dryrun` and `record`.
- `cursor.py` — JS injected via `Page.addScriptToEvaluateOnNewDocument`: draws a cursor div that follows `mousemove` and pulses on `mousedown` (pointer-events none, z-index max) so screencast/headless recordings show the pointer.
- `capture.py` — two backends. `ScreencastCapture` (default): CDP `Page.startScreencast` (jpeg, q80, maxWidth/Height = viewport), acks every frame, writes `raw/frames/NNNNNN.jpg` + timestamps, and on stop assembles `raw/capture.mp4` with the ffmpeg concat demuxer (per-frame `duration`) at 30 fps CFR (`-vf fps=30 -c:v libx264 -crf 18 -pix_fmt yuv420p`). `ScreenCapture`: OS grabber of the Chrome window bounds (Windows `gdigrab -i desktop -offset_x -offset_y -video_size`, macOS `avfoundation -i "<screen-idx>:none"` + crop, Linux `x11grab -i :0.0+x,y -video_size`), 30 fps, `-c:v libx264 -preset ultrafast -crf 18`, stopped by writing `q` to stdin. Both expose `start() -> t0`, `now() -> seconds since t0`, `stop() -> path`.
- `tts.py` — `synthesize(text, ref_wav, backend) -> (np.ndarray float32 mono, sr)`. Backends: `turbo` (`chatterbox.tts_turbo.ChatterboxTurboTTS.from_pretrained(device)`), `nano` (same with `nano=True`), `classic` (`chatterbox.tts.ChatterboxTTS`, uses `exaggeration`/`cfg_weight`), `tone` (synthetic: a quiet 220 Hz tone + amplitude envelope, length = words/2.5 s, for tests/dry runs; needs no ML deps), `auto` (cuda/rocm/mps → turbo, cpu → nano). Loads the model once per process. Writes WAV via soundfile at the model's `sr` (24000). Stats helper: duration, peak/rms dBFS, `silent` (rms < -50 dBFS), `clipped` (>0.5% samples at |1.0|).
- `narration.py` — template generation, JSON validation (`{"intro": str, "outro": str, "steps": [{"id","text"}]}`, ids must equal scenario step ids in order, per-segment ≤ 45 words, total words ≤ max_length_seconds * 2.6), LLM request via an OpenAI-compatible `/v1/chat/completions` POST (urllib only, no SDK) with a strict JSON-only instruction, one repair retry, then template fallback.
- `pacing.py` — pure functions: given `durations.json` and step order, compute planned start offsets: intro plays from t=0 while holding on the first screen; `step[i].t_start = max(prev.t_end + 0.3, prev.t_start + durations[prev.id])`; outro starts at `max(last.t_end, last.t_start + durations[last.id])`; capture ends 2.0 s after outro end. `record` uses this live (waits before each step); `edit` reads the actual times from `markers.json`.
- `markers.py` — schema: `{"capture_start_epoch": float, "intro_t": 0.0, "outro_t": float, "end_t": float, "steps": [{"id","t_start","t_end","status","wait_windows": [[t0,t1],...]}]}`.
- `edit.py` — build the final timeline from `markers.json`: (1) speed up any `wait_window` longer than 1.5 s by 4x only if that step's narration has already finished before the window starts, otherwise leave it; (2) place `seg-intro.wav` at `intro_t`, each `seg-<id>.wav` at its (remapped) `t_start`, `seg-outro.wav` at `outro_t`; (3) crop to the app viewport if the raw capture is larger than the viewport (screen backend); (4) `loudnorm=I=-16:TP=-1.5:LRA=11` on the narration bus; (5) mux H.264 `-crf 20 -pix_fmt yuv420p -movflags +faststart`, AAC 160k 48 kHz. Single ffmpeg filter_complex where possible; write the exact command to `logs/edit.json`.
- `verify.py` — with ffprobe (or the fallback parser): duration ≤ max_length_seconds + 10; |audio − video| ≤ 0.5 s; no black frame in the first and last 1.0 s (`blackdetect`); narration audible (`volumedetect` mean_volume > −30 dB); thumbnails at 10/50/90%. All results in `logs/verify.json` with pass/fail per check.
- `report.py` — `report.md` (verdict, step table, checks table, artifact paths, env summary) and `result.json`.
- `llm.py` — OpenAI-compatible helpers used by `check-model` and `narrate-llm` (urllib, timeouts, clear errors, never raises past the CLI boundary).
- `cli.py` + `__main__.py` — argparse subcommands mapping to the table above.

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
| `basic` (`username_env`, `password_env`, sent as HTTP basic auth).
Actions: `goto` (path or absolute URL), `click` (selector), `fill`
`{selector,text}`, `type` `{selector,text,delay_ms}`, `press` (key),
`upload` `{selector, files[]}`, `hover` (selector), `scroll` `{selector}` or
`{y}`, `wait` `{ms}`, `wait_for` `{selector|text, timeout_s}`, `screenshot`
(name). Expect: `text` (visible page text contains), `selector` (+ optional
`contains`, `count_min`), `url_contains`, `not_text`. Relative file paths are
resolved against the scenario file's directory. Step `timeout_s` default 60.

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
- Browser: headless Chromium (`/opt/pw-browsers/chromium-1194/chrome-linux/chrome` here) over CDP against `tests/fixtures/app` served by `http.server` on a random port: login form, file upload chips, ask → delayed answer with citation. Cover a PASS scenario and a FAIL scenario (wrong expectation) with exit code 2 and a failure report containing console errors.
- E2E (`tests/test_e2e_run.py`): `run` with `--tts tone --capture screencast --narration template --headless` against the fixture app → `final/*.mp4` exists, verify passes, `result.json.verdict == "PASS"`.
- Lint: `ruff check .` clean with default rules.

Test venv here: `/home/user/dev/.venv-smoke/bin/python` (playwright, pytest,
numpy, soundfile, ruff, imageio-ffmpeg installed). Never `pip install`
torch/chatterbox in this container; the ML backends are exercised by unit
tests only through a mocked import.

## Function contract (builders must match these exactly)

```python
# demo_smoke/env.py
def detect(base_url: str | None = None, model: str | None = None) -> dict        # doctor report
def find_ffmpeg() -> str                      # raises RuntimeError with install hint if none
def find_ffprobe() -> str | None
def find_chrome() -> str | None
def torch_device() -> str                     # "cuda" | "rocm" | "mps" | "cpu" | "none" (torch missing)
class Paths:                                  # Paths(out).raw/.audio/.clips/.final/.logs (Path), mkdirs on init
def media_info(path) -> dict                  # {"duration": float, "width": int, "height": int, "has_audio": bool, "audio_duration": float|None} via ffprobe or ffmpeg -i parsing

# demo_smoke/scenario.py
def load(path: str | Path) -> dict            # validated; adds "_dir" (Path) and resolves file paths; raises ScenarioError(msg)
def validate(data: dict) -> list[str]         # error messages, [] if valid
class ScenarioError(ValueError)

# demo_smoke/chrome.py
class ChromeSession:                          # context manager
    page; context; browser; cdp               # cdp = page.context.new_cdp_session(page)
    window_bounds: dict                       # {"x","y","width","height"} from Browser.getWindowBounds
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
def run_steps(page, scenario: dict, out: Path, clock=None, pacer=None, screenshot_prefix: str = "step") -> list[dict]
#   shared executor; clock() -> seconds since capture start (or None); pacer(step_index, step_id) blocks until this step may start

# demo_smoke/capture.py
class ScreencastCapture:  def __init__(self, session: ChromeSession, out: Path, fps: int = 30); start(); now() -> float; stop() -> Path
class ScreenCapture:      def __init__(self, session: ChromeSession, out: Path, fps: int = 30); start(); now() -> float; stop() -> Path
def make(kind: str, session, out: Path) -> ScreencastCapture | ScreenCapture

# demo_smoke/tts.py
def synthesize(text: str, ref_wav: Path | None, backend: str = "auto", device: str | None = None,
               exaggeration: float = 0.5, cfg_weight: float = 0.5) -> tuple["np.ndarray", int]
def synth_all(out: Path, ref_wav: Path | None, backend: str = "auto") -> dict      # reads audio/narration.json, writes seg-*.wav + audio/durations.json, returns durations
def audio_stats(wav, sr: int) -> dict         # {"duration","peak_dbfs","rms_dbfs","silent","clipped"}
def resolve_backend(backend: str) -> str      # "auto" -> "turbo"/"nano" by torch_device()

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
def plan_timeline(markers: dict, durations: dict, min_wait: float = 1.5, speed: float = 4.0) -> dict
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
class LLMError(RuntimeError)

# demo_smoke/cli.py
def main(argv: list[str] | None = None) -> int       # exit code; __main__ calls sys.exit(main())
```
