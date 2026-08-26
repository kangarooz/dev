# Episode 06 — Lexical vs Semantic Search

**Target runtime:** 4:00
**Gate:** you have a default pick for a basic FAQ workflow, and a reason for it.

---

## Cold open — 0:00

`[SAY]` "Back in episode two we said 'a search step goes and finds material' and moved on.
Time to open that up. `native:lexical_search` and `native:semantic_search` — and picking wrong
degrades the final answer in a way that's genuinely hard to diagnose later."

`[CALLOUT]` Bad retrieval ≠ obvious bug

---

## Run it — 0:30

`[ACTION]` Fresh session, bootstrap, paste prompt 6.

---

## The two — 1:00

`[SAY]` "Lexical matches words. You search for a term, it finds documents containing that
term. Exact, literal, predictable."

`[SAY]` "Semantic matches meaning. You search for a phrase, it finds documents that are
*about* that idea even when they don't share a single word with your query."

`[SAY]` "Neither is the better one. They fail in opposite directions, and that's the useful
part."

---

## Where each breaks — 1:45

`[ACTION]` Scroll to strengths and weaknesses.

`[SAY]` "Lexical misses paraphrase. The user says 'can't log in', the doc says 'authentication
failure', lexical returns nothing and your chat step answers from an empty context."

`[SAY]` "Semantic misses precision. The user asks about error code `E-4021` and semantic
cheerfully hands back five documents about errors in general, none of which mention that
code. Identifiers, part numbers, exact names — that's where semantic quietly lets you down."

`[CALLOUT]` Paraphrase vs precision

`[HOLD]` 4s.

---

## Downstream effect — 2:30

`[SAY]` "The prompt asks how each changes the quality of the downstream chat answer, and
that's the question that matters. The chat step can only work with what retrieval handed it.
Wrong retrieval doesn't produce an error — it produces a confident answer built on the wrong
three documents. You will spend real time blaming the prompt for what was a retrieval
problem."

`[CALLOUT]` Blame retrieval before the prompt

---

## Decision rule — 3:10

`[ACTION]` Scroll to the decision rule.

`[SAY]` "Get one you can apply without thinking. Something like: exact identifiers and known
terminology, start lexical. Users asking in their own words about a body of prose, start
semantic. Then check whether it also told you when to stop treating this as a binary — because
hybrid exists, and that's episode ten."

---

## Exit — 3:40

`[SAY]` "Move on when you can explain exact-match versus meaning-based retrieval, and you know
which one you'd reach for first on a basic FAQ. Next: the episode that makes you dangerous —
reading logs."
