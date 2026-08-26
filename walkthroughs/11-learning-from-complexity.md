# Episode 11 — Learning From Complexity Without Over-Learning

**Workflow:** On-Prem Jira Assistant
**Target runtime:** 4:30
**Section:** optional.

---

## Cold open — 0:00

`[SAY]` "This episode is less about a workflow than about a habit. The On-Prem Jira Assistant
is complex — genuinely, not accidentally — and the prompt is built around a specific risk:
that you read something sophisticated and conclude that sophistication is the standard."

`[CALLOUT]` Complex ≠ exemplary

---

## Run it — 0:30

`[ACTION]` Fresh session, bootstrap, paste prompt 11.

`[SAY]` "Look at how it's framed — teach me how to learn from a complex workflow without
copying it blindly. And the two questions that carry it: which complexity is justified by the
integration problem, and which shouldn't go into a first project."

---

## The critical path — 1:15

`[SAY]` "Get the main line through it before anything else. In a big workflow there's usually
a spine — the handful of steps that carry a request from arrival to answer — and then a lot of
steps hanging off it handling cases. Find the spine. Ignore the branches on your first pass."

`[CALLOUT]` Find the spine first

---

## Where complexity lives — 2:00

`[ACTION]` Scroll to that section.

`[SAY]` "Ask where it's concentrated, because it's rarely spread evenly. In an integration
workflow like this, complexity clusters at the boundary — talking to a system that has its own
schema, its own auth, its own failure modes, and no interest in being convenient."

`[SAY]` "That's justified complexity. It exists because Jira exists, on-prem, with a specific
configuration. It's not a design flourish."

---

## Justified vs optional — 2:50

`[SAY]` "Here's the distinction to carry out of this episode. Justified complexity is a
response to something real in the problem — an external system, a compliance requirement, a
volume of edge cases someone actually hit. Optional complexity is a response to something
imagined."

`[SAY]` "You can usually tell them apart by asking what would break if it were removed. If
the answer is specific — 'Jira returns a different shape when the field is empty' — it's
justified. If the answer is vague — 'it's more robust' — treat it as optional."

`[CALLOUT]` What breaks if I remove it?

`[ACTION]` Follow up live:

```
For each piece of complexity you called justified, tell me the specific thing
that breaks without it. If you can't name one, say so.
```

`[HOLD]` 8s.

---

## What to borrow — 3:50

`[SAY]` "Take the design habits, not the volume. How it handles a failing external call, how it
keeps the integration boundary separate from the reasoning — those transfer. The specific
accumulation of handling does not, and copying it into a first project gives you all the
maintenance cost of a mature workflow with none of the reasons."
