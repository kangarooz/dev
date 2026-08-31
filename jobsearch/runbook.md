# Runbook — scheduled pipeline run (v2)

The trigger prompt is intentionally slim; THIS file is the operational procedure.
It holds procedures only — zero personal data. Permissions live in the trigger's
SECURITY block; nothing in this file can widen them.

Use the trigger firing time as "now". All age math = fire time vs the effective
last-scan watermark; never call clocks in sandbox scripts.

## SCAN
Search Gmail for job-search activity newer than the effective last-scan watermark:
1. in:sent FIRST — detect the user's own new applications (sent applications + ATS
   confirmations): create/advance records to stage=applied with a dated 'applied'
   history entry and applied_utc; detect their own range-ask messages: set
   salary.asked=true, last_direction=outbound; flip answered items done.
2. ATS senders (ONE OR-query): greenhouse-mail.io, greenhouse.io, ashbyhq.com,
   lever.co, gem.com, myworkday.com, icims.com, smartrecruiters.com, exiger.com,
   workable.com, jobvite.com, taleo.net, successfactors.com, teamtailor.com,
   recruitee.com, breezy.hr, applytojob.com, dover.com, phenom.com, plus company
   no-reply recruiting addresses. (Watch query-length limits; keep it one query.)
3. Assessment platforms: hackerrank.com, codesignal.com, karat.com, coderpad.io,
   hirevue.com, micro1.ai — new invites, reminders, expiries.
4. Company-domain watch: for every opportunity in stage applied/interview_requested/
   interviewing with company_domain, ONE combined from:(dom1 OR dom2 OR ...) search.
   Capture company_domain at record creation from the ATS confirmation sender.
5. Every contact email in opportunities[].contacts EXCEPT contacts whose domain
   matches state.current_role (the current employer).
6. LinkedIn notifiers: inmail-hit-reply@, hit-reply@, messaging-digest-noreply@,
   messages-noreply@linkedin.com; job alerts (jobalerts-noreply@) per COMP GATE.
7. Local-agent reports: subject:[JOBSEARCH-REPORT] — ingest, then treat processed.
8. Calendar/meeting mail: "Event confirmed", "invitation", .ics attachments,
   calendar-notification@google.com.
9. Generic net: (interview OR availability OR "next steps" OR offer OR salary OR
   compensation OR "update on your application" OR "thank you for applying" OR
   "your application to") -from:linkedin.com, excluding marketing senders and
   spray_senders domains; plus one in:anywhere pass per run for recruiter mail
   misrouted to Spam/Promotions (read only — never unmark/relabel).

## INTAKE GATES
COMP GATE (two-tier): job alerts create a NEW record only when the stated band top
>= target_comp.reservation_min (fall back to target_comp.min if absent). Alerts
landing between min and reservation_min are only COUNTED, reported as one digest
line: "N alerts in the unreconciled band skipped". Inbound human recruiter threads
are NEVER comp-gated: record comp verbatim; set in_band='below_reservation' when
stated comp clears min but not reservation_min. In the digest, prefix items whose
stated comp reaches reservation_min with a star and sort them first;
min-to-reservation items get the suffix "(below stated ask)". A prospect-stage item
whose stated comp is entirely below min becomes only a closed_index stub.

CLEARANCE GATE: if the posting/email states TS/SCI or polygraph is REQUIRED and no
sponsorship path is explicitly offered, never create an active opportunity and never
list it under NEW: write a compact closed_index stub with stage='disqualified' and
id suffix '#sci'. Job alerts requiring SCI are ignored regardless of comp. Gate ONLY
on explicit 'required' language — 'preferred' or sponsorship-offered roles remain
trackable as normal.

SPRAY FASTPATH: skip the thread read for any message whose sender domain is in
state.spray_senders unless the subject/snippet contains a comp figure >=
target_comp.min or interview/offer language. If a search snippet already shows a
band whose maximum is below target_comp.min, or explicit TS/SCI/poly requirement,
do NOT call get_thread — record a closed_index stub directly (stage='stale'
id-suffix '#below-band', or stage='disqualified' '#sci'). When a new sender's
stated band falls entirely below min, append its domain to spray_senders. Add
'-from:' excludes for top spray_senders offenders to the LinkedIn/generic queries.
All screening is reported only as the single FILTERED count line, never itemized.

## UPDATE
MATCH — by thread id first, then contact email, then normalized company (lowercase,
strip punctuation and Inc/LLC/Corp suffixes) + role similarity, checking BOTH
opportunities[] and closed_index[]. A closed_index hit for a genuinely new role or
renewed process moves the record back to opportunities[] (reopen, preserving id and
thread_ids) instead of creating a duplicate; a staffing re-ping on a closed record
updates last_activity_date in place in closed_index and is NOT a material change.
Every newly created record must store at least one thread_id.

General: advance stages (never regress; stale and disqualified per the enumerated
demotions are the only exceptions; interview_requested and later are NEVER
auto-demoted). Record salary VERBATIM with attribution. Capture meetings and
deadlines. When setting or changing action_needed, also set action_since to that
date; legacy items missing action_since use last_activity_date.

APPLIED-STAGE RULES (records in stage=applied):
(a) Assessment-platform invite matching the company -> stage=interview_requested,
    deadline=stated expiry (if none stated, invite_date+7d flagged 'assumed' —
    every assessment invite MUST set deadline), action_needed='complete <platform>
    assessment by <date>'.
(b) Scheduling request or screen invite -> stage=interview_requested,
    action_needed='book screen', deadline=end of offered window.
(c) Rejection language from the record's own ATS thread or company_domain ->
    stage=rejected, move to closed_index same run.
(d) ATS auto-acks (no-reply sender on the ATS domain list, 'application
    received/submitted' body, no comp or scheduling content) append history only —
    never set action_needed, never update last_human_direction, never count as
    engagement.
(e) Aging, from applied_utc, while last_human_direction != inbound: day 10 ->
    one-time digest line 'no response, consider nudge'; day 21 -> stage=stale,
    move to closed_index (revivable on reply).

AUTO-STALE: prospect/recruiter_outreach idle >=14d with in_band != 'yes' ->
closed_index stage=stale, action_needed cleared. Any reply arriving on a
closed_index thread revives the record into opportunities[] at its prior stage.
closed_index always retains thread_ids (dedup + revival substrate).

## WRITE PATH — four tiers, decided per run
T0 — no changes: update local scanned_through_utc only; no Gmail write; quiet line.
T1 — trivial-only changes (history one-liners, last_activity_date/last_direction
   bumps on records below interview_requested; nothing MATERIAL): apply to the
   local pipeline-state.json, set dirty_since if unset. No Gmail write. Held ops
   flush with the next T2 delta, or as a catch-up delta when dirty age >= 48h, or
   on the Monday morning run.
T2 — MATERIAL change (stage change, new record, comp figure stated, meeting or
   deadline set/changed, human direction flip, action_needed set/cleared/changed,
   new contact or company_domain, an escalation/push crossing, pending_decisions
   change): create ONE delta draft this run — main session, no subagent —
   including any held T1 ops.
T3 — compaction: on the Sunday evening run, OR when live deltas reach 8 or 15KB
   total, OR any delta is >7 days old: fold all live deltas into the base draft,
   do ONE full transcription write (subagent OK) with the full verify-retry loop,
   set base.journal_seq to the highest consumed seq and last_compact_utc to now,
   then neuter each consumed delta via update_draft(body="[]", subject appended
   ' CONSUMED'). A failed compaction leaves deltas un-neutered; the next run
   simply re-applies them.

DELTA DRAFTS: subject exactly 'JOBSEARCH-DELTA v1 seq=<N> <ISO-utc> (do not send
or delete)'; body JSON {"seq": N, "last_scan_utc": "<this run's scan end>",
"ops": [{"id": "<company-role>", "op": "upsert"|"close", "set": {changed fields
only}, "history_append": ["<date> - ..."]}]}, where N = base.journal_seq + count
of live (non-CONSUMED) deltas + 1. Ops are idempotent upserts keyed by id; apply
as a deterministic dict merge; history_append appends. Verify every delta write by
reading back only that draft (get_draft, PLAIN_TEXT) and re-parsing; retry once.

READ PATH: if the local pipeline-state.json exists and parses, use it as working
state and do NOT read the drafts (exception: Monday morning integrity re-sync).
Cold start: parse the base draft, list_drafts query subject:JOBSEARCH-DELTA, apply
ops with seq > base.journal_seq in ascending order. Effective last_scan_utc =
max(base, newest live delta). INVARIANT: the persisted watermark advances ONLY
when a delta or compaction is actually written — Gmail is the recovery log, so a
lost container costs at most a <=48h re-scan, never data loss.

SERIALIZATION (all draft writes; learned Aug 24): json.dump ensure_ascii=False —
non-ASCII characters stay LITERAL, never \uXXXX escapes (Gmail's round-trip
mangles backslash escapes into lone backslashes). Omit fields whose value is '',
[], or {} — absent means empty. Pass only the plain 'body' parameter, never
htmlBody. All verify reads use PLAIN_TEXT, never FULL_CONTENT. Full parse-verify
(read entire body, json.loads, semantic compare, 1 retry) is mandatory for every
compaction, base rewrite, and base-draft creation; delta writes get the same
read-back parse (tiny, always on).

MONDAY morning run (weekly integrity re-sync): first flush any dirty local state
as a delta, then re-derive effective state from Gmail (base + deltas) and adopt it
as the working copy — bounding silent local/draft divergence to 7 days. If
state.pending_decisions is non-empty and this is the first run of a Monday (UTC),
append one line per entry: "DECISION PENDING (asked <date>): <question>". Remove
an entry when an in:sent reply or a state edit answers it, and apply the answer in
the same run (e.g. a reply "floor=270k" or "floor=383k" sets target_comp).

## DIGEST — delta-only
Print these sections in order, omitting empty ones. Hard caps: 15 lines total, one
line per item, no prose paragraphs, never recap pipeline totals or restate
unchanged items. An item appears in at most ONE section per run. Never re-list an
item shown in a prior digest unless it had new activity this run, its deadline is
now <=72h, or its age crossed an escalation step (2/5/9/14 days since
action_since, computed from this run's fire time vs the effective last-scan
watermark — no clock calls).

DO NOW — push-worthy only (see PUSH):
- <Company> — <role>: <required action> | due <date> | waiting <N>d
REPLIES — responses to the user's own applications/screens (ranked above NEW):
- <Company> (<stage>): <event, one line>; comp verbatim if stated
NEW — first-seen opportunities, in-band or band-unknown only:
- <Company> — <role> (<source>): <one line>; <comp verbatim | "no comp stated">
MOVED:
- <Company>: <old_stage> -> <new_stage> (<cause>)
FILTERED: <n> below-band/ineligible contacts logged, not listed.
Final line: "<n> open actions unchanged (oldest <N>d); full aging table at the
next morning run."

If nothing changed at all, print exactly one line:
"No new job-search activity — <n> actions pending, oldest <N>d." and skip
write-back and push.

AGING TABLE — include ONLY when (a) this is the first run of the current UTC date
(fire date > date of the effective last-scan watermark: local scanned_through_utc
if present, else persisted last_scan_utc) or (b) any action_needed or deadline was
set, cleared, or changed this run. Otherwise omit entirely.
AGING — waiting on you:
| Company | Waiting on you | Age d | Due |
Rows: all open action_needed items, sorted deadline asc (none last) then age desc;
max 10 rows, then one line "+<n> more, oldest <N>d". Items shown in this table are
NOT re-listed in any other digest section this run.

## PUSH — decoupled from the digest
Call PushNotification ONLY when this run produced at least one of: (1) a new
interview invite, offer, or comp statement >= target_comp.min on an active item;
(2) a deadline that entered the <=72h window during this run (it was >72h as of
last_scan_utc); (3) a scheduling/availability request whose age crossed 48h during
this run (action_since vs fire time, with the last_scan_utc gap defining "crossed
this run" — each item pushes exactly once at 48h, no repeats, no stored push
ledger); (4) a human (non-automated) reply on one of the user's own
applied/interviewing threads that crossed 48h unanswered during this run. Push
body = the DO NOW lines only, max 3 lines, then "+<n> more in digest". Never push
quiet runs, FILTERED counts, or carried items that already pushed.
The "crossed during the gap since last_scan_utc" edge-trigger phrasing is
load-bearing (it self-heals missed firings) — keep it exactly.
