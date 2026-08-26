# Episode 04 — `depends_on` vs `conditions`

**Target runtime:** 4:30
**Gate:** you can explain *when* a step runs versus *whether* it runs, and you don't panic at
a skipped step.

---

## Cold open — 0:00

`[SAY]` "Two fields, and new SAs mix them up constantly. `depends_on` controls *when* a step
runs. `conditions` controls *whether* it runs at all. Timing versus permission. Once that
lands you'll never confuse them again, and you'll debug twice as fast — because the two
produce different symptoms in the logs."

`[CALLOUT]` When vs whether

---

## Run it — 0:30

`[ACTION]` Fresh session, bootstrap, paste prompt 4.

`[SAY]` "The prompt asks for both explanations, an example of each, how they combine, what a
skipped step means, and — the useful bit — what log symptoms point at one versus the other."

---

## depends_on — 1:10

`[ACTION]` Scroll to the `depends_on` section.

`[SAY]` "`depends_on` is a data-readiness statement. This step needs something an earlier step
produces, so don't start it until that step has finished. It's not about ordering for
neatness — it's about a value existing in state before someone reads it."

`[SAY]` "Which connects straight back to episode three. Every `depends_on` you write is you
saying: the notebook needs this page filled in first."

---

## conditions — 1:50

`[ACTION]` Scroll to `conditions`.

`[SAY]` "`conditions` is a branch. This step is only relevant in some runs — if the user asked
a certain kind of question, if the search came back empty, if a flag was set. When the
condition isn't met, the step doesn't run, and that is a normal, healthy outcome."

---

## Side by side — 2:30

`[ACTION]` Scroll to the comparison. Leave it on screen.

`[HOLD]` 6s.

`[SAY]` "In one workflow you'll see both on the same step: wait for the search to finish,
*and* only run if the search found something. Those are two independent gates. Passing one
tells you nothing about the other."

`[ACTION]` Follow up live:

```
Show me a real step from this repo that has both depends_on and conditions,
and tell me what each one is protecting against.
```

`[CALLOUT]` Two gates, independent

---

## Skipped is not failed — 3:20

`[SAY]` "Here's the part to actually remember. You'll open a run, see a step marked skipped,
and assume you've found the bug. Usually you haven't. A skipped step means a condition
evaluated false — which is the feature working."

`[SAY]` "The question to ask isn't 'why did this skip', it's 'should this have skipped, given
what this run's input was'. Different question, and it points you at the condition instead of
sending you off rewriting a step that was fine."

`[CALLOUT]` Skipped ≠ broken

---

## Symptoms — 4:00

`[ACTION]` Scroll to the debugging section.

`[SAY]` "Rough shapes: a dependency problem tends to look like a step running with something
missing or empty. A condition problem looks like a step that never ran at all. Empty versus
absent. That's your first fork in the road when you open a log, which is the whole subject of
episode seven."

---

## Exit — 4:20

`[SAY]` "Move on when *when versus whether* is automatic and a skipped step doesn't make you
flinch. Next: reading a real workflow end to end."
