# Episode 07 — Reading Logs Like A Debugger

**Target runtime:** 5:00
**Gate:** your first move on a broken workflow is gathering evidence, not guessing.

---

## Cold open — 0:00

`[SAY]` "Last one in the core path, and it's the one that changes how you work. Everything so
far taught you how workflows are supposed to behave. This one teaches you what to do when one
doesn't — and specifically how to stop guessing."

`[CALLOUT]` Evidence, not guesses

---

## Run it — 0:30

`[ACTION]` Fresh session, bootstrap, paste prompt 7.

`[SAY]` "The valuable section here is number four: telling apart a missing-state problem, a
skipped-branch problem, a tool configuration problem, and a timeout. Four causes that all
look like 'the workflow didn't work' from the outside."

---

## Zoom out first — 1:10

`[ACTION]` Scroll to the execution-level section.

`[SAY]` "Start at the run level, not the step level. Which steps ran, which were skipped,
which failed, how long each took. You're building a shape of the run before you dive into any
one part of it."

`[SAY]` "The instinct is to jump straight to the step that produced the bad output. Resist it.
That step is usually the victim, not the culprit — it answered badly because something
upstream handed it nothing."

`[CALLOUT]` The loud step is usually the victim

---

## Then zoom in — 2:00

`[SAY]` "Now go step level. Inputs, outputs, status, duration. And read the *inputs* first.
Everyone reads outputs first because that's where the wrongness is visible. The inputs are
where the wrongness started."

---

## The four symptoms — 2:45

`[ACTION]` Scroll to the symptom-to-cause guide. Leave it up.

`[HOLD]` 8s.

`[SAY]` "Match these to what you already know. Missing state — a step ran with an empty or
absent input, straight out of episode three. Skipped branch — a step didn't run because a
condition said no, episode four, and possibly correct. Tool config — the step ran, the tool
was reached, and it came back with something unusable. Timeout — duration tells you before
anything else does."

`[SAY]` "Empty versus absent versus unusable versus slow. Four fingerprints."

`[CALLOUT]` Empty · absent · unusable · slow

---

## A repeatable loop — 3:45

`[ACTION]` Scroll to the debugging workflow section.

`[SAY]` "Write this one down somewhere you'll see it. The value isn't that it's clever — it's
that it's the same every time, so under pressure you don't have to invent an approach. Run
level, then step level, then inputs before outputs, then match the fingerprint."

---

## Work a real failure — 4:20

`[SAY]` "Then do it for real. Take a failed run — any failed run — and work backward through
it out loud before you look at anyone else's diagnosis. Being wrong here is cheap and it's
how the pattern-matching gets built."

---

## Exit — 4:45

`[SAY]` "That's the core path. Seven episodes and you can read a workflow, trace its state,
tell timing from branching, choose retrieval, and debug from evidence. The next five are the
optional section — more complex shapes, and the judgement to not copy them blindly."

`[CALLOUT]` Core path complete
