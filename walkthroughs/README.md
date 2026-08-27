# Socrates SA Onboarding — Walkthrough Video Series

Recording scripts for the [Socrates Drop-In Onboarding Experience For New Solution
Architects](https://yurts-team.atlassian.net/wiki/spaces/Enablement/pages/1099923458/Socrates+Drop-In+Onboarding+Experience+For+New+Solution+Architects)
(Enablement space, page `1099923458`).

The Confluence doc is deliberately prompt-only — it hands a new SA thirteen blocks of text
and trusts them to know what to do with each one. These videos close that gap: they show the
prompt being run, what a good response looks like, and how to tell you have actually learned
the thing before moving on.

## Series shape

| # | Episode | Target | Gate |
|---|---------|--------|------|
| 00 | Setup & Bootstrap | 4:00 | Builder responds and then stops |
| 01 | The Onboarding Map | 3:30 | Can state first concepts, in order |
| 02 | The Simple FAQ Workflow | 5:00 | Can explain the workflow from memory |
| 03 | Making State Concrete | 5:30 | Knows plain keys vs `$input.` / `$state.` |
| 04 | `depends_on` vs `conditions` | 4:30 | Can explain *when* vs *whether* |
| 05 | Reading A Real Workflow | 5:30 | Can say why the subagent exists |
| 06 | Lexical vs Semantic Search | 4:00 | Has a default pick for a basic FAQ |
| 07 | Reading Logs Like A Debugger | 5:00 | First move is evidence, not guessing |
| 08 | The Many Roles of `native:chat` | 5:00 | — |
| 09 | A Real RAG Workflow (`unified_search`) | 4:30 | Recognizes `unified_search` in the wild |
| 10 | Hybrid / Full-Text / Unified | 4:00 | Knows the escalation signals |
| 11 | Learning From Complexity | 4:30 | — |
| 12 | Reviewing Your First Draft | 4:00 | — |

Episodes 00–07 are the core path and should be recorded first; they are the ones a new SA
watches in their first week. 08–12 are the optional second section.

## Before you record

1. **Clone the repo.** `git clone git@gitlab.com:yurtsai/enablement-workflow-configs.git`
   The `socrates-sa-onboarding` and `legion-agent-builder` skills live there, along with
   every workflow JSON referenced in the series.
   > The Confluence doc spells this `enablement-workfow-config` — a typo. The real remote is
   > `enablement-workflow-configs`. Worth fixing on the page; until then, expect new SAs to
   > hit a clone failure on their very first step.
2. **VPN on.** Several referenced surfaces are internal-only.
3. **Pick one agent and stay with it** across all thirteen episodes. The doc says "Codex,
   OpenCode, or your agent of choice" — switching mid-series makes the UI jump around and
   costs the viewer more than the neutrality gains.
4. **Fresh session per episode.** Each script assumes an empty context window except where
   it explicitly says to continue the prior conversation.

## Conventions used in these scripts

- **`[SCREEN]`** — what the viewer is looking at.
- **`[ACTION]`** — what your hands do. Every paste, click, and scroll.
- **`[SAY]`** — narration. Written to be read aloud at ~150 wpm; the timings assume it.
- **`[HOLD]`** — stop talking and let the viewer read. Always specify seconds.
- **`[CALLOUT]`** — a text overlay in post. Keep to six words or fewer.

Responses from a live agent are non-deterministic. The scripts never quote an expected
response verbatim — they tell you what to look for and what to do if it is missing. If a
take produces a genuinely bad answer, keep it: episode 01 and 12 both budget time for
showing a weak response and steering out of it, which is more useful than a clean take.

## Recording the screen

`record/` turns these scripts into videos — see [record/README.md](record/README.md).

```
cd record && npm install
npm run record -- --target fixture --all --mp4      # offline, works anywhere
npm run record -- --target lol --all --mp4          # the real app; needs VPN + one-time auth
```

The scripts stay the source of truth: edit the markdown, re-run, get new videos. Narration
becomes on-screen captions and a timed WebVTT track; `[CALLOUT]` beats become overlays and
section headings become chapter cards, so a take needs no editing pass to be watchable.

Two ways to record, and they produce the same video structurally:

- **`--target lol`** — the live platform. This is what you publish. Has to run on a machine
  with the VPN connected.
- **`--target fixture`** — an offline reconstruction of the agent-builder UI (terminal, chat
  screen, JSON viewer). Same beats, same pacing, same overlays; the pixels behind them are a
  stand-in. Good for reviewing a script's pacing before booking time on the real app, and it
  is the only mode that runs in CI or a cloud container.

Manual capture still works if you prefer it — these scripts were written to be read off a
second monitor, and nothing about the harness stops you.
