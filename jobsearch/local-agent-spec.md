# Local agent spec — LinkedIn in-app + Outlook feeder (OpenClaw)

The cloud routine cannot reach LinkedIn's in-app inbox or a personal Outlook
mailbox (egress policy + no connector for personal accounts). Those two
surfaces are covered by a job on the user's own machines (OpenClaw over the
tailnet), which reports findings BY EMAIL into Gmail, where the cloud routine
ingests them. Email is the message bus; no network path between the two is
needed.

## Job definition (run 1-2x daily on either machine)

1. LinkedIn (logged-in browser session): open the messaging inbox; for each
   thread with activity since the last run, capture sender name, their
   company/role if shown, message text, thread URL, and date. Include
   recruiter threads only; skip social chatter.
2. Outlook (personal account, logged-in): search the last 3 days for
   job-search mail (interview, availability, offer, recruiter domains);
   capture sender, subject, date, body text.
3. Send ONE email to nickwilliams92@gmail.com:
   - Subject: `[JOBSEARCH-REPORT] YYYY-MM-DD HH:MM`
   - Body: a single JSON array of records:

```json
[
  {
    "source": "linkedin" | "outlook",
    "from_name": "...",
    "from_company": "...",
    "role": "...",
    "date": "ISO",
    "text": "full message text",
    "link": "thread URL (linkedin only)",
    "comp_mentioned": "verbatim numbers or empty"
  }
]
```

4. If nothing new: send nothing (the cloud routine treats absence as no news).

## Rules for the local job

- Read-only everywhere. It must never reply, connect, or apply on LinkedIn —
  automation that ACTS on LinkedIn risks account restriction; reading your own
  inbox and self-reporting by email is the conservative footprint.
- Never include credentials or cookies in the report email.
- Keep the JSON valid; the cloud routine parses it mechanically.
