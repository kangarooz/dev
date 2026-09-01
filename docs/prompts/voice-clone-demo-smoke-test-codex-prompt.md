# Voice-Cloned Demo Smoke Test — drop-in Codex prompt

Paste everything below the line into a fresh Codex session on the machine that
can reach the feature under test. Fill in the `<<...>>` placeholders first.
First run takes 1–2 hours (installs + model download). Reruns take minutes.

Pipeline: Chatterbox TTS (voice clone) + PyTorch/Torchaudio → Chrome DevTools
Protocol drives the live feature → FFmpeg screen capture, edit, mix, encode →
Loom upload → smoke-test verdict + shareable link.

---

You are running a full end-to-end demo smoke test of a new feature and
producing a narrated walkthrough video in my cloned voice. Work autonomously.
Do not stop to ask questions unless a placeholder below is missing or a
credential is genuinely unavailable. Install what you need. Keep going until
you hand me a Loom link and a PASS/FAIL verdict.

## Inputs (fill these in)

- FEATURE_NAME: <<short name, e.g. "Image RAG for WPC">>
- FEATURE_DESCRIPTION: <<2–4 sentences: what it does and what "working" looks like>>
- APP_URL: <<http(s)://host:port of the running app>>
- LOGIN: <<how to authenticate — env var names, a 1Password item, or "none">>
- FIXTURES: <<absolute paths to any files to upload/drag in during the demo>>
- HAPPY_PATH: <<numbered user steps, e.g. 1) open X 2) upload Y 3) ask Z 4) confirm citation appears>>
- EXPECTED_RESULTS: <<what must be visible on screen for each step to count as passing>>
- REFERENCE_AUDIO: <<absolute path to a 60–90 s clean WAV/MP3 of my voice, single speaker, no music>>
- OUTPUT_DIR: <<absolute path, e.g. ~/demos/<feature-slug>>>
- VIDEO_TITLE: <<title for the Loom>>
- MAX_LENGTH_SECONDS: 90

## Phase 0 — Environment (skip anything already present)

1. Detect OS and print it. Create OUTPUT_DIR with subfolders: `raw/`, `audio/`, `clips/`, `final/`, `logs/`.
2. Confirm or install: Python 3.10+, `ffmpeg` 6+ (prefer 9.x), Google Chrome or Chromium, Node 18+.
3. Create a venv in OUTPUT_DIR/.venv. Install `torch`, `torchaudio` (CUDA build if an NVIDIA GPU exists, else CPU), and `chatterbox-tts`. Verify import works and print the device it will use.
4. Install a CDP client (`playwright` for Python is fine; do NOT use its bundled browser — connect to the real Chrome over CDP so the recording shows the real UI).
5. Launch Chrome with `--remote-debugging-port=9222`, a fresh `--user-data-dir` under OUTPUT_DIR, window size 1920x1080, positioned at 0,0. Confirm `http://localhost:9222/json/version` responds.
6. Log every command and its output to `logs/setup.log`. If any install fails, fix it and retry; do not silently downgrade.

## Phase 1 — Voice clone check

1. Load Chatterbox with REFERENCE_AUDIO as the voice prompt. Generate a 10-second test line: "This is a voice check for the FEATURE_NAME demo." Save to `audio/voice_check.wav` (24 kHz mono).
2. Report duration, peak dBFS, and whether the output is clipped or silent. If it sounds wrong (silent, >2x expected length, obvious artifacts by inspecting the waveform stats), retry with `exaggeration=0.5`, `cfg_weight=0.5`, and if needed trim REFERENCE_AUDIO to its cleanest 30 s.

## Phase 2 — Dry-run the feature (the actual smoke test)

1. Using CDP, navigate to APP_URL, authenticate per LOGIN, and execute HAPPY_PATH step by step. After each step take a full-page screenshot to `logs/step-NN.png` and check EXPECTED_RESULTS for that step against the DOM and the screenshot.
2. Wait for real completion signals (network idle, spinner gone, expected text present), not fixed sleeps. Cap each step at 120 s.
3. Record a step table to `logs/smoke-results.md`: step, action, expected, observed, PASS/FAIL, screenshot path, wall time.
4. If any step FAILS: capture console errors, failed network requests (URL, status, response body excerpt), and the page HTML. Try once more from the beginning. If it fails again, STOP the video pipeline, write the failure report, and hand me the report — a demo of a broken feature is not the goal.
5. If all steps PASS: note the timing of each step so the narration can be paced to it.

## Phase 3 — Script the narration

1. Write a narration script to `audio/script.md`: one short paragraph per HAPPY_PATH step plus a 1-sentence intro and a 1-sentence close. Conversational, first person, present tense, no filler, no marketing language. Target total spoken length ≤ MAX_LENGTH_SECONDS at ~150 wpm.
2. Each paragraph must reference something visible on screen at that moment ("I drag the four PDFs in", "the citation shows up on the right").
3. Generate each paragraph as its own file with Chatterbox: `audio/seg-NN.wav`. Print each segment's duration. Regenerate any segment that is silent, clipped, or >1.6x its expected length.

## Phase 4 — Record the walkthrough

1. Start an FFmpeg screen capture of the Chrome window region only (gdigrab on Windows, avfoundation on macOS, x11grab on Linux), 30 fps, lossless or high-quality intermediate (`-c:v libx264 -preset ultrafast -crf 18`), to `raw/capture.mkv`. No microphone input.
2. Re-run HAPPY_PATH via CDP exactly as in Phase 2, but pace it: before each step, wait until the previous narration segment's duration has elapsed (use the durations from Phase 3) so the action lands while the matching sentence is playing. Move the mouse to targets with smooth CDP `Input.dispatchMouseEvent` moves so the cursor is visible and readable. Write the wall-clock timestamp of each step start to `logs/markers.json`.
3. Stop the capture 2 s after the final expected result appears.

## Phase 5 — Edit, mix, encode

1. Using `logs/markers.json`, cut the capture into per-step clips and remove dead time longer than 1.5 s (loading spinners can be sped up 4x rather than cut, so the viewer sees the wait was real).
2. Crop to the app viewport, no OS chrome or taskbar. Keep 16:9 at 1920x1080.
3. Build the narration track by placing each `seg-NN.wav` at its step's start; pad with silence between segments. Loudness-normalize to -16 LUFS integrated, -1.5 dBTP (`loudnorm`).
4. Mux to `final/<feature-slug>.mp4`: H.264 (`libx264 -crf 20 -pix_fmt yuv420p -movflags +faststart`), AAC 160k, 48 kHz.
5. Sanity checks, print the results: total duration ≤ MAX_LENGTH_SECONDS + 10, audio and video durations match within 0.5 s, first and last frames are not black, narration audible (mean volume > -30 dB). Fix and re-encode if any check fails.
6. Extract three thumbnails (10%, 50%, 90%) to `final/` and view them to confirm the right screens are shown.

## Phase 6 — Publish

1. Upload `final/<feature-slug>.mp4` to Loom via the browser (loom.com → New video → Upload a video) using the same CDP-controlled Chrome. Title it VIDEO_TITLE. Set the description to the intro sentence plus the step list from `audio/script.md`.
2. Wait for processing to finish, copy the share link, and confirm the share page loads for a logged-out request.

## Deliverable (print this at the end)

- Loom link
- Smoke test verdict: PASS or FAIL, with the step table from `logs/smoke-results.md`
- Video length, file path, file size
- Anything you changed in the environment (packages installed, Chrome flags, models downloaded) so the next person can rerun this in minutes
- Anything that looked flaky even though it passed

Rules: never fabricate a passing step; never edit the app under test; never
narrate something that did not happen on screen; keep all artifacts in
OUTPUT_DIR.
