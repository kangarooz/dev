# Episode 00 — Setup & Bootstrap

**Target runtime:** 4:00
**Gate:** the builder acknowledges the brief and then *stops*, waiting for you.

After this episode a new SA has the repo cloned, the two skills available, and a session
primed with the bootstrap prompt — the state every other episode assumes.

---

## Cold open — 0:00

`[SCREEN]` Empty terminal. Nothing else on screen.

`[SAY]` "The Socrates onboarding guide is thirteen blocks of text on a Confluence page. It
doesn't tell you what to run them in, and it doesn't tell you what a good answer looks like.
That's what this series is for. In this first one we get you set up, and we paste the
bootstrap prompt — the one that changes how the agent talks to you for everything after."

`[CALLOUT]` Setup once, then twelve prompts

---

## Clone the repo — 0:25

`[SAY]` "Everything the guide references lives in one repo. The skills, and every workflow
JSON we're going to read together."

`[ACTION]` Type it out, don't paste — it is short and the viewer will be typing along:

```
git clone git@gitlab.com:yurtsai/enablement-workflow-configs.git
cd enablement-workflow-configs
```

`[HOLD]` 3s on the clone succeeding.

`[SAY]` "One warning. The Confluence page spells this repo `enablement-workfow-config` —
missing an `l`, missing the `s`. If you copy the name off the page, the clone fails and it
looks like you don't have access. You do. It's a typo on the page."

`[CALLOUT]` Page typo: -workfow-config

`[ACTION]` Show the two skill directories so the viewer knows they are real files, not magic:

```
ls .claude/skills/
```

`[SAY]` "`socrates-sa-onboarding` teaches in a deeper, beginner-first way.
`legion-agent-builder` is the one that actually knows Legion's workflow structure. You want
both loaded, every session, for this whole series."

---

## Start the session — 1:20

`[SCREEN]` Agent starting up, empty context.

`[SAY]` "Use Codex, OpenCode, or whatever agent you already have. I'm using [NAME]. Pick one
and stay with it for the whole guide — the prompts are portable, but you'll learn the tool
faster if you don't keep switching."

`[ACTION]` Confirm the skills are visible to the agent before pasting anything. If your agent
lists skills, show that list. If it doesn't, say so plainly rather than pretending.

`[SAY]` "If your agent can't see those two skills, stop here and fix it. Every prompt after
this one leans on them, and without them you'll get generic LLM answers about workflows in
general instead of answers about Legion."

`[CALLOUT]` No skills = generic answers

---

## Paste the bootstrap — 2:00

`[ACTION]` Paste the bootstrap prompt from the Confluence page. Scroll it slowly, top to
bottom, while narrating.

`[SAY]` "Read what this is actually doing, because it's the most important paste in the
series. It's not asking a question. It's setting the terms for every answer that follows."

`[SAY]` "Ground answers in the real docs and real workflow JSONs, not the model's general
knowledge. Explain like I just started, not like I'm an expert. Define Legion vocabulary the
first time you use it. Go one level deeper than a summary — what it is, how it works, why it
matters. And the one that matters most —"

`[ACTION]` Highlight the line: *do not give me a compressed answer that is mostly labels,
file paths, or bullet points without explanation.*

`[HOLD]` 4s.

`[SAY]` "That line exists because that's the failure mode. Ask an agent about a workflow and
it hands you a file path and a field name and calls it teaching. This prompt forbids that.
If an answer later in the series comes back as a wall of paths, the bootstrap didn't take —
start a fresh session and paste it again."

`[CALLOUT]` Paths aren't teaching

---

## The stop instruction — 3:10

`[ACTION]` Highlight the final line: *Do not do anything further until prompted to do so.*

`[SAY]` "Last line, and it's doing real work. Without it, a helpful agent reads all those
teaching instructions and immediately starts teaching — and you've lost control of the
pacing before you've asked your first question."

`[ACTION]` Send. Wait for the response on camera. Do not cut.

`[SAY]` "What you want back is short. An acknowledgement, and then nothing. If it launches
into a lecture about Legion workflows right now, that's your signal the stop instruction got
lost — most often because it was pasted as several messages instead of one."

`[HOLD]` 5s on the acknowledgement.

---

## Exit — 3:40

`[SAY]` "That's your baseline session. Keep it open — the next episode runs prompt one in
this same window. And the rhythm for the whole guide is: one prompt at a time, keep talking
to it as long as you need, move on when you can explain the thing without scrolling back up."

`[CALLOUT]` One prompt at a time
