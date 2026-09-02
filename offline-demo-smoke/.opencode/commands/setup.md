---
description: Check this machine for the offline demo smoke kit and print the setup command to run (scripts/setup.*)
agent: demo-smoke
---
Get this machine ready for the offline demo smoke kit. Extra arguments given to this command (pass them through to the setup script unchanged): $ARGUMENTS

Doctor output right now, before setup:
!`python -m demo_smoke doctor`

1. If the doctor output above starts with `doctor: ok`, skip to step 3.
2. Otherwise do NOT run the setup script yourself (it installs packages, which your rules forbid). Reply with the exact command for the user's operating system and ask them to run it in a terminal, then tell you when it finished:
   - macOS / Linux: `bash scripts/setup.sh $ARGUMENTS` (options: `--tts` installs voice cloning, `--torch cuda|rocm|cpu|auto`, `--prefetch auto|turbo|nano|classic|none` caches the Chatterbox weights, `--model qwen3-coder:30b` pulls the LLM with Ollama)
   - Windows: `powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 $ARGUMENTS` (options: `-Tts`, `-Torch cuda|rocm|cpu|auto` (rocm falls back to cpu on Windows), `-Prefetch auto|turbo|nano|classic|none`, `-Model qwen3-coder:30b`)
   The script creates `.venv`, installs `requirements.txt`, with `--tts`/`-Tts` also torch + `requirements-tts.txt` and runs `python -m demo_smoke prefetch`, optionally `ollama pull`, and prints its own doctor output at the end. Stop here until the user says it is done.
3. Run `python -m demo_smoke doctor --base-url http://localhost:11434/v1` (use `.venv/bin/python` or `.venv\Scripts\python.exe` instead of `python` if that file exists), then read `demo-output/logs/doctor.json`.
4. Reply with one line each for: os, python, ffmpeg, chrome, torch_device, chatterbox, tts_auto, tts_ready, llm reachable, and every `hint` entry verbatim. Do not try to fix anything yourself; tell the user what to install or start.
