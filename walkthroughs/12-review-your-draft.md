# Episode 12 — Reviewing Your First Draft

**Target runtime:** 4:00
**Use when:** you have a draft FAQ workflow and want structure feedback before human review.

---

## Cold open — 0:00

`[SAY]` "Last one, and it's the only episode where you bring something of your own. You've got
a draft FAQ workflow — semantic search, chat, send-message. Before you put it in front of a
senior SA, run this."

`[CALLOUT]` Bring your own draft

---

## Run it — 0:30

`[ACTION]` Have the draft open. Fresh session, bootstrap, paste prompt 12.

`[SAY]` "It asks for highest-risk issues first, and the categories should look familiar by
now: state management, dependencies, conditions and fallback, tool fit, likely skipped
branches, failure modes, and what breaks first in the logs. That's the whole core path, turned
into a checklist against your own work."

---

## Read it in order — 1:10

`[SAY]` "Highest-risk first matters. The temptation is to start with whatever's easiest to
fix, and you'll feel productive doing it while the state bug at the top sits there. Work down
from the top."

`[SAY]` "And read every finding against your own file before you accept it. You've spent
eleven episodes learning to check claims against source. Don't stop on the one that's about
your own workflow."

`[CALLOUT]` Check it against your file

---

## The smallest set of changes — 2:00

`[ACTION]` Scroll to that section.

`[SAY]` "This is deliberate. Not 'everything you could improve' — the smallest set that
improves it most. A first draft that gets rewritten wholesale on review feedback usually comes
back worse, because you've changed ten things at once and can't tell which one helped."

---

## Then test — 2:45

`[SAY]` "It tells you what to test immediately after. Do that before touching anything else.
Change a small number of things, run it, read the log with episode seven's loop, confirm the
change did what you expected."

`[SAY]` "That cycle — small change, run, read evidence — is the actual job. Everything in
this series has been building toward being able to do it without guessing."

---

## Over-revision — 3:20

`[SAY]` "Last question in the prompt asks what mistake to avoid if you revise too
aggressively, and it's the right note to end on. A working simple workflow beats an elaborate
one you can't debug. If review turns your four-step FAQ into a twelve-step branching thing,
you've lost more than you gained."

`[CALLOUT]` Simple and working wins

---

## Series close — 3:40

`[SAY]` "That's the guide. Thirteen prompts, and the point of all of them was to make you
someone who checks rather than guesses. Go build the FAQ workflow, break it, and read the
logs. That's where the rest of it gets learned."
