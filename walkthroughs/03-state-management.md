# Episode 03 — Making State Concrete

**Target runtime:** 5:30
**Gate:** you can explain plain keys vs `$input.` vs `$state.`, and trace state through a
workflow you didn't write.

---

## Cold open — 0:00

`[SAY]` "State is where new SAs lose the most time, and it's not because it's conceptually
hard. It's because there are three or four ways to refer to the same value and they aren't
all valid in the same places. This episode is the one to slow down on."

`[CALLOUT]` The episode to slow down on

---

## Run it — 0:30

`[ACTION]` Fresh session, bootstrap, then paste prompt 3.

`[SAY]` "Seven things asked for, and the two that carry the episode are number three —
normal state versus `initial_state` versus `persist_keys` — and number four, the reference
syntax. Everything else is context around those two."

---

## The mental model — 1:10

`[ACTION]` Scroll to the plain-English section.

`[SAY]` "Simplest version: state is a shared notebook that lives for the length of one run.
A step reads what earlier steps wrote, does its work, writes its own result back. Nothing is
passed hand to hand — it all goes through the notebook."

`[SAY]` "If the answer gave you an analogy, keep it. You'll be using it when you explain this
to the next new SA."

---

## The three kinds — 2:00

`[SAY]` "Now the distinction that actually bites."

`[ACTION]` Scroll to `initial_state` / `persist_keys`.

`[SAY]` "Normal state is what steps write while the run is happening. `initial_state` is what's
already in the notebook before step one runs. `persist_keys` is about what survives past the
end of the run."

`[SAY]` "Different lifetimes. And the bug you'll write is assuming something is in the
notebook at the start when nothing put it there — because it *was* there yesterday, in a run
where an earlier step wrote it."

`[CALLOUT]` Three lifetimes, one notebook

`[ACTION]` Follow up live:

```
Show me a concrete case where a step reads a key that is only ever written by
another step, and what the run looks like when that other step is skipped.
```

`[HOLD]` 6s.

---

## Reference syntax — 3:20

`[ACTION]` Scroll to the reference rules. This is the section to put on screen longest.

`[SAY]` "Plain key reference, `$input.` reference, `$state.` reference. They look
interchangeable. They are not — each is valid in specific places, and using the wrong one
doesn't always error. Sometimes it just resolves to nothing and your step runs with an empty
value."

`[HOLD]` 8s on the rules. Don't narrate over this — let them read it.

`[SAY]` "Screenshot that. Genuinely. It's the single highest-value thing on your screen this
week."

`[CALLOUT]` Screenshot this one

---

## Tracing checklist — 4:30

`[ACTION]` Scroll to the tracing checklist.

`[SAY]` "This is what you'll actually use day to day: a repeatable way to walk a workflow
someone else wrote and figure out where a value came from. Pick any workflow in the repo and
run the checklist against it before you move on. Ten minutes, and it converts all of this
from something you read into something you can do."

---

## Exit — 5:05

`[SAY]` "Move on when you can explain the three lifetimes and the three reference forms
without looking. Next: the difference between a step waiting, and a step deciding."
