---
description: Synthesize one test sentence in the cloned voice (arg - reference WAV path, inside the kit directory)
agent: demo-smoke
---
Check that voice cloning works with the reference clip `$1` (a path inside the kit directory, e.g. `voice/ref.wav`; clips outside it trigger a permission prompt).

1. Run `python -m demo_smoke voice-check --ref $1 --out demo-output/voice-check --tts auto` and wait for it to finish (the first run loads the model and can take a minute).
2. Read `demo-output/voice-check/logs/voice-check.json`.
3. Reply with: backend, duration, peak_dbfs, rms_dbfs, silent, clipped, seconds_to_synthesize, and the path of `voice_check.wav`. Tell the user to listen to that file.
4. If the command exited with code 3 and the message mentions chatterbox, torch, or Hugging Face: say that `requirements-tts.txt` is not installed or the weights were not prefetched, quote the exact `python -m demo_smoke prefetch --tts <backend>` command from the error message (the backend `auto` chose is in `demo-output/voice-check/logs/voice-check.json` as `backend`) to run while online, and that `--tts tone` tests the rest of the pipeline without a voice.
5. If `silent` or `clipped` is true: suggest a cleaner reference clip of 30 to 90 seconds, one speaker, no music, speech starting within the first second, peaks around -3 dBFS.
