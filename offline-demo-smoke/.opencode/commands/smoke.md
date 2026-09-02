---
description: Smoke-test a scenario and record the narrated MP4 (args - scenario path, optional output dir)
agent: demo-smoke
---
Run the full demo smoke playbook for one scenario.

- Scenario file: $1
- Output directory: $2
- Extra arguments: $ARGUMENTS

If the output directory line above is empty, use `demo-output/<slug>` where `<slug>` is the `"slug"` field of the scenario.
If the extra arguments contain the word `headless`, add `--headless` to the dryrun and record commands.
If the extra arguments contain a path ending in `.wav`, or the user mentioned a reference voice WAV, pass it as `--ref <path>` in the synth step; otherwise omit `--ref`.

Scenario contents:
@$1

Read `$1` with the read tool now if its contents are not visible above.

Follow the Playbook from your instructions, in this order, one command per step, reading `<out>/logs/<cmd>.json` after each:
doctor -> dryrun -> write `<out>/audio/narration.json` (with the write tool, the whole file at once; never bash, never a heredoc, never the edit tool) -> narrate-validate -> synth -> record -> edit -> verify -> Report.
Stop and write the Report as soon as a command exits with code 2 or 3.
