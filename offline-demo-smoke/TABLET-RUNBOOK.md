# Tablet runbook (for the local Claude Code session)

Purpose: finish the kit setup on this Windows machine, prove the pipeline, and
report back to the cloud session named `dev-0a [835fa6]` with SendMessage.
Everything here is idempotent. Do NOT edit kit source files and do NOT `git push`;
only run things and report. Read `README.md` in this directory if anything is unclear.

Background: an admin bootstrap already installed Git, Python 3.11, Node, OpenCode,
Claude Code and created `.venv`, but its window died during the long setup step.

All commands are PowerShell, run from this directory (`offline-demo-smoke`).

1. `git pull --ff-only` first (the branch is being updated from the cloud), then
   confirm `.venv\Scripts\python.exe` exists.

2. Kit setup (torch CPU + Chatterbox + voice-model weights; 20-60 minutes). Run it
   in the background and poll the output instead of blocking on it:

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Tts -Torch auto -Prefetch auto -BaseUrl http://127.0.0.1:1234/v1 -NoDoctor
   ```

   If it fails, capture the last 40 lines. A transient pip/network error may be
   retried once. Anything else: report it and continue with the steps that do not
   need Chatterbox.

3. Doctor:

   ```powershell
   .venv\Scripts\python.exe -m demo_smoke doctor --out demo-output\doctor
   ```

   Report the summary line and the contents of `demo-output\doctor\logs\doctor.json`
   (ffmpeg, chrome, torch_device, chatterbox, tts_ready, opencode).

4. LM Studio (do not start LM Studio yourself; if unreachable, say so):

   ```powershell
   (Invoke-RestMethod http://127.0.0.1:1234/v1/models).data.id
   .venv\Scripts\python.exe -m demo_smoke check-model --base-url http://127.0.0.1:1234/v1 --list
   .venv\Scripts\python.exe -m demo_smoke check-model --base-url http://127.0.0.1:1234/v1 --model <first id>
   ```

   Report the ids and the tool-calling PASS/FAIL.

5. Proof run on the bundled mock app (no LLM, synthetic voice). Start the app
   server in the background from `tests\fixtures\app`:

   ```powershell
   .venv\Scripts\python.exe -m http.server 8765 --bind 127.0.0.1
   ```

   then from `offline-demo-smoke`:

   ```powershell
   .venv\Scripts\python.exe -m demo_smoke run tests\fixtures\scenarios\fixture-pass.json --out demo-output\fixture --tts tone --headless
   ```

   Report the exit code, the `run:` summary line, and whether
   `demo-output\fixture\final\fixture-pass.mp4` exists (size; duration from
   `demo-output\fixture\result.json`). Stop the http.server afterwards.

6. Voice check, only if doctor said `tts_ready` is true AND a reference clip exists at
   `voice\ref.wav`:

   ```powershell
   .venv\Scripts\python.exe -m demo_smoke voice-check --ref voice\ref.wav --out demo-output\voice-check --tts auto
   ```

   Report its stats. No clip: say so and skip.

7. Machine facts: hostname, Windows version, CPU, RAM, GPU name
   (`Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name`),
   free space on C:.

8. Send ONE consolidated report to `dev-0a [835fa6]` with SendMessage. First line:
   `Tablet setup report: <PASS/FAIL summary>`. Send an interim message only if the
   setup fails and cannot be recovered. Also summarize what you did for the user in
   your own session.
