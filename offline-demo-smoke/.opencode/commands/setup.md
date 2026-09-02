---
description: Check this machine for the offline demo smoke kit and print the setup command to run (scripts/setup.*)
agent: demo-smoke
---
Get this machine ready for the offline demo smoke kit. Extra arguments given to this command (pass them through to the setup script unchanged): $ARGUMENTS

Doctor output right now, before setup (the kit's venv interpreter when it exists, else python3, else python):
!`if [ -x .venv/bin/python ]; then .venv/bin/python -m demo_smoke doctor; elif command -v python3 >/dev/null 2>&1; then python3 -m demo_smoke doctor; else python -m demo_smoke doctor; fi`

In this command an exit code 3 from doctor (`doctor: PROBLEMS`) is expected and is not a stop condition: do not write the smoke Report, follow the steps below.

1. If the doctor output above starts with `doctor: ok`, skip to step 3. If it is not a line starting with `doctor:` at all (for example `command not found`, `No module named`, a Windows shell error), run `python -m demo_smoke doctor` yourself first, with `.venv/bin/python` or `.venv\Scripts\python.exe` instead of `python` when `ls .venv/bin/python` / `dir .venv\Scripts\python.exe` prints the path, and judge that output instead. If the output contains `chrome=MISSING`, tell the user to install Google Chrome/Chromium or set `DEMO_SMOKE_CHROME` (the setup script cannot install Chrome) and continue with step 3.
2. Otherwise do NOT run the setup script yourself (it installs packages, which your rules forbid). Reply with the exact command for the user's operating system and ask them to run it in a terminal, then tell you when it finished:
   - macOS / Linux: `bash scripts/setup.sh $ARGUMENTS` (options: `--tts` installs voice cloning, `--torch cuda|rocm|cpu|auto`, `--torch-index URL`, `--prefetch auto|turbo|nano|classic|none` caches the Chatterbox weights, `--model qwen3-coder:30b` pulls the LLM with Ollama, `--python PATH`, `--base-url URL`, `--no-doctor`; `--help` lists them)
   - Windows: `powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 $ARGUMENTS` (options: `-Tts`, `-Torch cuda|rocm|cpu|auto` (rocm falls back to cpu on Windows), `-TorchIndex URL`, `-Prefetch auto|turbo|nano|classic|none`, `-Model qwen3-coder:30b`, `-Python PATH`, `-BaseUrl URL`, `-NoDoctor`)
   The script creates `.venv`, installs `requirements.txt`, with `--tts`/`-Tts` also torch + `requirements-tts.txt` and runs `python -m demo_smoke prefetch`, optionally `ollama pull`, and prints its own doctor output at the end. Stop here until the user says it is done.
3. Run `python -m demo_smoke doctor --base-url http://localhost:11434/v1` (with the venv interpreter from step 1 when it exists), then read `demo-output/logs/doctor.json`. Exit 3 here only means something is missing or Ollama is not running; go on to step 4.
4. Reply with one line each for: os, python, ffmpeg, chrome, opencode, torch_device, chatterbox, tts_auto, tts_ready, llm reachable, and every `hint` entry verbatim. Do not try to fix anything yourself; tell the user what to install or start.
