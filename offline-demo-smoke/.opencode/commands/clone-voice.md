---
description: Record a reference voice with the microphone (record-ref), then synthesize a test sentence in the cloned voice (voice-check); arg - optional voice name (TUI only)
agent: demo-smoke
---
Record a reference clip of the user's voice and check that Chatterbox can clone it. Voice name from the user (may be empty): $1

This command needs the `question` tool and only works in the interactive TUI (`opencode`, then `/clone-voice`). If the question tool is not in your tool list, reply exactly: `clone-voice needs the TUI: run "opencode" in the kit directory, then type /clone-voice` and stop.

One tool call per step. Never ask for any secret. Use `python`, or the venv interpreter from your instructions, in every command.

1. Run `python -m demo_smoke devices --out demo-output/voice`. It prints `audio inputs (sounddevice):` with one line per microphone, `[<index>] <name>`, a `*` marking the default, and the screens. If it prints `unavailable: ...` under audio inputs, tell the user to `pip install sounddevice` in the venv (Linux also `libportaudio2`) and that `record-ref` will fall back to ffmpeg; continue.
2. Question with two entries: (a) header `Microphone`, question `Which microphone should I record from?`, options: one per listed input as `[<index>] <name>` with the default first and `(Recommended)` appended, plus `Default input`; (b) header `Voice name`, question `What should this voice be called? (file name: voices/<name>.wav)`, options: the name above if it is not empty, otherwise `mine (Recommended)`. Remember `<index>` (none for `Default input`) and `<name>` (lowercase, dashes, no spaces).
3. Tell the user, in one message: the recording lasts 60 seconds, starts after a 3-2-1 countdown, they should read the passage that will be printed, in a quiet room, close to the microphone, at normal speaking volume. Then run `python -m demo_smoke record-ref --out voices/<name>.wav --device <index>` (omit `--device <index>` for the default input) with the bash tool `timeout` set to 240000 ms; it needs about 70 seconds, so it is not timed out before that.
   - Exit 0 (`record-ref: ok ...`): continue with step 4.
   - Exit 4 (`record-ref: WARN ...`, the file is still saved): read `voices/<name>.json` with the read tool and quote its `warnings`. Ask the question, header `Recording`, `The recording has warnings (see above). Record again or keep it?`, options: `Record again (Recommended)`, `Keep it`. On `Record again` run step 3 once more (once only), otherwise continue with step 4.
   - Exit 3 (`error: record-ref: ...`): quote the line. If it mentions sounddevice or PortAudio, say `pip install sounddevice` (Linux: also `libportaudio2`), or run with `--backend ffmpeg --device "<device name>"`; if it mentions ffmpeg or no device, tell the user to check the microphone in the OS settings. Stop.
   - Exit 130 (`interrupted`): say the recording was cancelled and stop.
4. Run `python -m demo_smoke voice-check --ref voices/<name>.wav --out demo-output/voice-check --tts auto` (the first run loads the model and can take a minute; set the bash tool `timeout` to 600000 ms).
5. Read `demo-output/voice-check/logs/voice-check.json` with the read tool.
6. Reply with the verdict and the numbers:
   - `Reference: voices/<name>.wav` with `duration`, `speech_seconds`, `snr_db`, `clipped_pct` from `voices/<name>.json` and its `warnings` (or `none`).
   - `Clone: ` with `backend`, `duration`, `peak_dbfs`, `rms_dbfs`, `silent`, `clipped`, `seconds_to_synthesize` and the path of `voice_check.wav`; tell the user to listen to it.
   - `Verdict: GOOD` when there are no warnings and `silent` and `clipped` are both false; otherwise `Verdict: RE-RECORD` with the reason (noisy: snr below 15 dB; short: under 20 s of speech; clipped) and the advice: a quieter room, closer to the microphone, lower input gain, start speaking within the first second, then run `/clone-voice <name>` again.
   - If voice-check exited 3 and the message mentions chatterbox, torch or Hugging Face: `requirements-tts.txt` is not installed or the weights were not prefetched; quote the exact `python -m demo_smoke prefetch --tts <backend>` command from the error message (to run while online), and say that the recording itself is fine and `--tts tone` tests the rest of the pipeline without a voice.
   - Last line: `Next: /smoke scenarios/<slug>.json demo-output/<slug> voices/<name>.wav` (the scenario the user wants; `/onboard` creates one).
