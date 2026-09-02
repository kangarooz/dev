---
description: Smoke-test a scenario and record the narrated MP4 (args - scenario path, optional output dir)
agent: demo-smoke
---
Run the full demo smoke playbook for one scenario.

- Scenario file: $1
- Output directory: $2

If the output directory line above is empty, use `demo-output/<slug>` where `<slug>` is the `"slug"` field of the scenario.
If the user mentioned a reference voice WAV, pass it as `--ref` in the synth step; otherwise omit `--ref`.

Scenario contents:
@$1

If the scenario contents are not shown above, read the scenario file first.

Follow the Playbook from your instructions, in this order, one command per step, reading `<out>/logs/<cmd>.json` after each:
doctor -> dryrun -> write `<out>/audio/narration.json` (with the write tool, the whole file at once; never bash, never a heredoc, never the edit tool) -> narrate-validate -> synth -> record -> edit -> verify -> Report.
Stop and write the Report as soon as a command exits with code 2 or 3.
