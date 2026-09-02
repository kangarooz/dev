---
description: Write and validate audio/narration.json for a scenario (args - scenario path, output dir; both required)
agent: demo-smoke
---
Write the narration for this demo, then validate it. Do not run any other pipeline step.

- Scenario file: $1
- Output directory: $2 (required; normally `demo-output/<slug>`)

Scenario:
@$1

Dry-run results (only the words inside quotes in the `observed` fields may be spoken; never the selectors, counts or URLs):
@$2/logs/dryrun.json

1. If the dry-run results are not shown above, read `$2/logs/dryrun.json`. If that file does not exist, run `python -m demo_smoke dryrun $1 --out $2` first and read it.
2. Write `$2/audio/narration.json` with the write tool, the whole file at once (never bash, never a heredoc, never the edit tool), following the Narration rules in your instructions (same step ids in the same order, at most 45 words per segment, first person, present tense, mention the step title or a quoted expectation).
3. Run `python -m demo_smoke narrate-validate $1 --out $2`. On exit code 4 (`narrate-validate: INVALID`), rewrite the whole file once with the write tool, fixing exactly the listed errors, and validate once more. If it is still invalid, run `python -m demo_smoke narrate-template $1 --out $2` and say that the template was used.
4. Reply with the final narration text per segment and the `narrate-validate:` summary line.
