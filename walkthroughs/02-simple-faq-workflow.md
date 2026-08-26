# Episode 02 — The Simple FAQ Workflow

**Target runtime:** 5:00
**Gate:** you can explain the workflow from memory and name the minimum state flow.

---

## Cold open — 0:00

`[SAY]` "This is the workflow every other Legion workflow is a variation on. User asks
something, you go find relevant material, you generate an answer from it, you send the answer
back. Four moves. Learn these four properly and complex workflows stop looking complex —
they start looking like this one with extra steps bolted on."

`[CALLOUT]` Four moves, endlessly reused

---

## Run it — 0:35

`[ACTION]` Fresh session. Paste the bootstrap first (briefly — don't re-explain it, just show
it going in), then paste prompt 2.

`[SAY]` "Note what the prompt asks for that a summary wouldn't give you: the minimum shape
that still works, the critical state flow step by step, and what would break if one piece
went missing. That last one is the teaching question. You learn what a piece does by learning
what its absence looks like."

---

## The four steps — 1:20

`[ACTION]` As the response comes in, scroll to the execution flow.

`[SAY]` "Walk it with me. User input arrives. A search step goes and finds material. An
answer-generation step turns that material into prose. A send step puts it in front of the
user."

`[SAY]` "The thing to internalize is that these steps don't talk to each other directly. The
search step doesn't hand its results to the chat step. It writes them somewhere, and the chat
step reads them from that somewhere. That somewhere is state, and it's the whole subject of
the next episode."

`[CALLOUT]` Steps don't talk. They share state.

---

## State flow — 2:15

`[ACTION]` Scroll to the state and dependencies section.

`[SAY]` "Ask yourself, for each step: what does it need to already exist before it can run,
and what does it leave behind for whoever comes next? That's the entire mental model."

`[SAY]` "Track the key names it gives you. Real names, from a real workflow — not
placeholders. If the response is using `some_state_key` and `your_output_here`, it's teaching
you the shape but not the substance. Ask it to redo the walkthrough against an actual FAQ
workflow in the repo."

`[ACTION]` Show the follow-up going in:

```
Redo the state walkthrough using a real FAQ-shaped workflow from this repo.
Name the actual step ids and state keys, and tell me the file path.
```

`[HOLD]` 6s on the answer.

---

## What the user sees — 3:30

`[SAY]` "The prompt asks what the user would see in chat when this works, and it's worth not
skipping. You're going to spend a lot of time looking at JSON, and it's easy to lose track of
the fact that all of this exists to put a paragraph of text in front of a person who asked a
question."

---

## What breaks — 4:00

`[ACTION]` Scroll to the failure section.

`[SAY]` "Here's the payoff question. If the search step's results never land in state, the
chat step still runs — it just answers from nothing, confidently. That's the single most
common shape of a broken Legion workflow: not a crash, an answer that's fluent and
unsupported."

`[CALLOUT]` Not a crash. A confident wrong answer.

`[SAY]` "Which is why episode seven is about reading logs. A workflow that fails loudly is
easy. This one fails quietly."

---

## Exit — 4:40

`[SAY]` "Move on when you can draw these four steps from memory and name the state that has
to exist between each pair. Next: state itself, properly."
