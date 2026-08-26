# Episode 09 — A Real RAG Workflow (`unified_search`)

**Workflow:** `Modules/IT HelpDesk/it_helpdesk_tier0_agent.json`
**Target runtime:** 4:30
**Gate:** you recognize what `native:unified_search` is doing when you meet it, and why a
workflow adds `native:attributions`.

---

## Cold open — 0:00

`[SAY]` "The IT HelpDesk tier-zero agent. This is a current, in-use workflow rather than a
teaching example, and it's built on `native:unified_search` — which is what you graduate to
after lexical-or-semantic stops being enough."

`[CALLOUT]` A real one, in use

---

## Open it first — 0:25

`[ACTION]` Open the JSON cold and scroll it once, as in episode 05.

`[SAY]` "Same discipline. And this one's bigger than the templated-writing workflow, so notice
your own reaction to the size — because the second half of this episode is about which of that
complexity you should ignore."

---

## Run it — 1:00

`[ACTION]` Fresh session, bootstrap, paste prompt 9.

---

## What unified_search does — 1:40

`[SAY]` "Episode six left you choosing between lexical and semantic. `unified_search` is the
step that stops making you choose up front — one retrieval step covering what you'd otherwise
wire together yourself."

`[SAY]` "Which means: fewer decisions for you, less visibility into which part of retrieval
found the thing. That's the trade, and it's worth naming while you're still learning."

`[ACTION]` Follow up live:

```
When unified_search returns a poor result set, how do I tell which part of the
retrieval underperformed? What would I look at in the run?
```

`[CALLOUT]` Convenience costs visibility

---

## attributions — 2:40

`[SAY]` "`native:attributions` is about where the answer came from — tying claims back to the
sources behind them. Sounds like polish. It isn't."

`[SAY]` "Go back to episode two's quiet failure: a chat step answering fluently from nothing.
Attributions are how that stops being invisible. An answer with no sources behind it looks
different from one with three, and the user can see the difference without reading a log."

`[CALLOUT]` Attributions make quiet failure visible

---

## What not to copy — 3:30

`[ACTION]` Scroll to the complexity sections.

`[SAY]` "The prompt splits this deliberately: what's worth learning from, and what shouldn't
go into a first project. Take that split seriously. This workflow earned its complexity over
time against real tickets and real users. Your first workflow hasn't earned any yet."

`[SAY]` "Copy the retrieval shape. Copy attributions. Leave the accumulated edge-case handling
where it is until you've hit those edges yourself."

---

## Exit — 4:15

`[SAY]` "Move on when you'd recognize `unified_search` in an unfamiliar workflow and could say
why someone put attributions in front of the final answer. Next: the full retrieval menu."
