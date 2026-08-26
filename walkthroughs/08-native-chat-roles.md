# Episode 08 — The Many Roles of `native:chat`

**Target runtime:** 5:00
**Section:** optional — after the core path.

---

## Cold open — 0:00

`[SAY]` "So far `native:chat` has been the step that writes the answer. That's one job. It's
not the only one, and once you see the others you start recognizing a whole class of workflow
you couldn't read before."

`[CALLOUT]` Chat isn't only for answers

---

## Run it — 0:30

`[ACTION]` Fresh session, bootstrap, paste prompt 8.

`[SAY]` "This prompt is doing something the earlier ones didn't — it asks the agent to go find
two architecturally *different* examples, and it explicitly says don't give me two simple
answer bots. If both examples it returns are FAQ-shaped, push back and make it look again."

---

## The synthesizer — 1:20

`[SAY]` "First role, the familiar one. Material comes in, prose goes out, the user reads it.
Note the configuration around it: model choice, prompt style, `template_vars`, what it depends
on. Those choices are tuned for producing readable text."

---

## The transformer — 2:10

`[SAY]` "Second role, and this is the one that expands your vocabulary. Here chat is doing
parsing, extraction, structured transformation, routing support, intermediate reasoning —
work in the *middle* of a workflow whose output no user ever reads directly."

`[SAY]` "Something unstructured arrives, and a later step needs it structured. Rather than
writing brittle parsing, you hand it to a model and ask for the shape you want. The output
goes into state and a downstream step consumes it."

`[CALLOUT]` Chat as a middle step

---

## Why not a simpler step — 3:00

`[ACTION]` Scroll to that part of each analysis.

`[SAY]` "The prompt asks why chat was a better fit than a plain utility or parser, and you
should be genuinely skeptical of the answer. Sometimes it's right: the input is too variable
for rules. Sometimes it isn't, and a deterministic step would have been cheaper, faster, and
wouldn't occasionally invent a field."

`[SAY]` "'Could this have been a regex' is a fair question to ask of any chat step you meet."

`[CALLOUT]` Could this have been a regex?

---

## Side by side — 3:50

`[ACTION]` Scroll to the comparison and the rule of thumb.

`[SAY]` "You want a rule you can apply in the moment. Roughly: is this step producing
something a human reads, or something another step consumes? Synthesis versus transformation.
The prompt style, the model choice, and how much you constrain the output all follow from
that one answer."

---

## Don't overgeneralize — 4:30

`[SAY]` "Last thing the prompt asks for, and don't skip it: what should you *not* take from
the complex example. Seeing chat used as a transformer is a license to use it that way when
the input is genuinely messy — not a license to put a language model in the middle of every
workflow you write."
