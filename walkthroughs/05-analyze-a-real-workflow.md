# Episode 05 — Reading A Real Workflow

**Workflow:** `Dev/templated-writing/document-creation/workflow.json`
**Target runtime:** 5:30
**Gate:** you can say why the subagent exists and name the key state written before the main
chat step.

---

## Cold open — 0:00

`[SAY]` "Four episodes of concepts. Now a real file. This one is `templated-writing` /
`document-creation`, and it was picked for the guide because it's small enough to hold in your
head and still does something architecturally interesting."

`[CALLOUT]` First real file

---

## Open it first — 0:30

`[ACTION]` Open the JSON in an editor *before* pasting the prompt. Scroll through the whole
thing once, at reading speed, without commentary.

`[HOLD]` 10s.

`[SAY]` "Look at it cold first. You won't understand it and that's fine — the point is that
when the explanation comes back, you're matching it against something you've seen rather than
taking it on faith. Do this every time. An agent's summary of a file you've never opened is
a story you have no way to check."

`[CALLOUT]` Look before you ask

---

## Run it — 1:15

`[ACTION]` Fresh session, bootstrap, paste prompt 5.

`[SAY]` "Seven things asked, and notice how many are about *why* rather than *what*. Why does
the subagent exist. Why is this a good teaching example. The what you could get from reading
the file. The why is what you're here for."

---

## The pattern — 2:00

`[ACTION]` Scroll to the architecture section, editor open beside it.

`[SAY]` "Find the overall shape first — intake, then processing, then generation, then
output, roughly. Then place each step in the file into that shape. If a step doesn't fit any
box you've drawn, your shape is wrong, not the workflow."

---

## Why the subagent — 2:45

`[SAY]` "This is the gate question for the episode, so let's be precise about it. A subagent
exists to take a self-contained chunk of work out of the main flow. The main flow stays
readable; the messy bounded part happens somewhere else and comes back as a result."

`[ACTION]` Follow up live:

```
Rewrite this workflow in plain English as if the subagent did not exist and all
its work happened inline. What specifically gets worse?
```

`[HOLD]` 8s.

`[SAY]` "That question is the one that makes it click. You understand why a boundary is there
by imagining the version without it."

`[CALLOUT]` Imagine it without the boundary

---

## State to the chat step — 3:45

`[ACTION]` Scroll to the state flow walkthrough.

`[SAY]` "Trace it from source intake to final output, and note every key the main
`native:chat` step depends on. Those keys are the contract: everything upstream exists to
populate them, and if any one is empty, that chat step produces something fluent and wrong —
exactly the quiet failure from episode two."

`[ACTION]` Point at the actual step ids in the editor as they're named.

---

## Lessons — 4:45

`[SAY]` "Ask for the three design lessons worth copying, and hold them loosely. This is one
workflow. Episode eleven is entirely about the trap of over-learning from a single example."

---

## Exit — 5:10

`[SAY]` "Move on when you can say why the subagent exists in your own words, and name what
state has to be in place before the main chat step fires. Next: how the search step actually
finds things."
