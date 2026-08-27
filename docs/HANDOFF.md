# Freshdesk Migration Tool — Handoff Document

## What this tool does
Migrates data between two Freshdesk accounts (FD → FD).
Phases (in dependency order): custom_fields → agents → companies → contacts → groups → tickets → canned_responses → knowledge_base.

Fully resumable via SQLite checkpoint (`migration.db`). Re-running skips already-done records.

## Current state (as of 2026-07-02)

### Tested and working
- Tickets: 1,000-ticket test passed with zero failures (counts/status/subject only — see CRITICAL FIX below)
- 2026-07-02: 7-ticket live verification on fixed code — bodies, attachments (3/3), requester
  emails, conversation counts, status/priority/group all match source, probe-verified
- Idempotency: re-run correctly skips already-migrated tickets (via `fd-migration-{id}` marker tag)
- Resume: crash mid-run resumes correctly from the checkpoint
- `--delta` mode: live-verified — new source note synced to target, zero duplicates
- Throughput: ~24 tickets/min on trial accounts (faster on Pro/Enterprise plans)
- Knowledge base phase: code exists in `fdmigrate/phases/kb.py` — NOT yet tested

### CRITICAL FIX (2026-07-02) — tickets migrated with EMPTY bodies
The ticket LIST endpoint (`/tickets`) does NOT return `description`, `attachments`,
or `requester` — so every previously migrated ticket had an empty body and lost its
ticket-level attachments (confirmed live: all 7 spike tickets, 3 attachments lost).
The old 1k test only verified counts/status/subject, so it never caught this.

Fix (in `fdmigrate/phases/tickets.py`): the create path now fetches the per-ticket
DETAIL (`GET /tickets/{id}?include=requester`) and builds the payload/attachments
from that. Costs 1 extra API call per new ticket. Verified end-to-end 2026-07-02:
the 7 spike tickets were deleted, re-migrated, and probe-checked — all bodies,
attachments and requesters intact.

### Bugs — ALL FIXED 2026-07-02 (kept for history)

**BUG #1 — Contacts 30k cap — FIXED**
`iter_windowed()` added to `client.py` (generic `updated_since` windowing);
`iter_contacts()` delegates to it; `contacts.py` now iterates
`ctx.src.iter_contacts(ctx.cfg.contacts_updated_since)`. Config gained
`contacts.updated_since` (default `2010-01-01T00:00:00Z`).

**BUG #2 — Companies 30k cap — MITIGATED**
`/companies` does NOT support `updated_since`, so windowing is impossible.
`paginate()` now logs a loud TRUNCATED warning at the 300-page cap, and
`check` reports `30000+ (30k list cap reached)`. >30k companies needs a
different strategy (rare in practice).

**BUG #3 — Reconciliation report incomplete — FIXED**
`reconciliation.csv` is now the FULL accounting: one row per idmap record across
ALL entities (source_id, target_id, status, error). The old 25-ticket random API
sample moved to its own report, `reports/ticket_spot_check.csv`
(`run.spot_check_sample`, default 25).

**BUG #4 — No agent pre-validation — FIXED**
`check` now compares source vs target agent emails (honouring `mapping.agents`)
and prints an AGENT GAP warning list before any migration runs.

---

## Enhancement list (status as of 2026-07-02)

| # | Priority | Description | Status |
|---|---|---|---|
| 1 | P1-BLOCKER | Contacts 30k windowing | DONE |
| 2 | P1-BLOCKER | Companies 30k windowing | MITIGATED (API can't window; loud warning at cap) |
| 3 | P1-BLOCKER | Fix reconciliation report | DONE (full accounting + separate spot-check) |
| 4 | P1 | Agent pre-validation in check command | DONE |
| 5 | P2 | Heartbeat in contacts phase | DONE |
| 6 | P2 | Heartbeat in companies phase | DONE |
| 7 | P2 | ETA in Heartbeat output | open |
| 8 | P2 | Cache _prefetch_target_markers in SQLite | open |
| 9 | P2 | --dry-run flag | open |
| 10 | P2 | Completion summary with total runtime | open |
| 11 | P3 | Delta catch-up of already-migrated tickets | DONE (`run --delta` + cursor, live-verified) |
| 12 | P3 | KB article attachments | open |
| 13 | P3 | reset --entity <name> subcommand | open |
| 14 | P3 | Windows long-run guidance | open |

New since 2026-07-02:
- `tickets.created_date_custom_field` config — stamps the source `created_at` into a
  (pre-existing, text) target custom field, per the common "original created date" ask.
- `company_id` mapped onto migrated tickets (with a 400-retry that drops the field
  if the target rejects it).
- Conversation failures now downgrade the ticket to `partial` with an error count
  instead of silently marking it `done`.

---

## Client migrations in pipeline

### Migration 1
- Tickets: 156,122 (no attachments, conversations included)
- Contacts: 206,007
- Groups: 5, Agents: 8
- Former blocker BUG #1 (contacts cap) fixed 2026-07-02
- Estimated runtime: 2–4 days at Pro/Enterprise rate limits

### Migration 4 (mid-size, NA → EU pod)
- Tickets: ~15,000 | Contacts: ~2,200 | Companies: ~190 | Groups: 7 | Agents: 11 | KB: 8 articles
- Freshdesk NA → EU pod, 7-day timeline
- Next step: replicate testing at these exact counts, entity by entity, before the client run
- §4.9 optional "original created date in custom field" → supported via
  `tickets.created_date_custom_field` (create the text custom field in the target first)

### Migration 2 — SDK Infinity Technologies (Confidential)
- Tickets: 52,500 (with attachments + conversations)
- Contacts: 2,050, Companies: 21, Groups: 11
- Also includes: 110 KB articles
- No scale blockers — ready to run after basic testing
- Estimated runtime: 1–2 days

### Migration 3
- Scope unknown as of 2026-06-26

---

## Architecture overview

```
fdmigrate/
  client.py       — FreshdeskClient: rate-limiting, pagination, iter_tickets(), attachments
  store.py        — SQLite state: idmap, conversations, attachments, events, cursors
  config.py       — Config dataclass + YAML loader
  runner.py       — Orchestration: check(), run_migration()
  reconcile.py    — Post-run reconciliation report
  backfill.py     — fix-assignments: backfill agent/group after the fact
  phases/
    base.py       — Context, Heartbeat, helper functions
    agents.py     — Match source agents to target by email
    companies.py  — Create/match companies by name
    contacts.py   — Create/update contacts, dedup by email
    groups.py     — Create/match groups by name
    tickets.py    — Tickets + conversations + attachments (the heavy phase)
    canned.py     — Canned responses
    kb.py         — Knowledge base: categories → folders → articles
    custom_fields.py — Discover and map custom fields
```

## How to run

```bash
# 1. Check connections
python -m fdmigrate check --config config.yaml

# 2. Sample run (50 tickets to validate)
python -m fdmigrate run --config config.yaml --only tickets --limit 50

# 3. Full run
python -m fdmigrate run --config config.yaml

# 4. Resume after crash (just re-run — checkpoint handles it)
python -m fdmigrate run --config config.yaml

# 5. Status check
python -m fdmigrate status --config config.yaml

# 6. Re-run a single phase
python -m fdmigrate run --config config.yaml --only contacts

# 7. Delta catch-up (after cutover: sync new replies/status on already-migrated
#    tickets updated in the source since the last run — no duplicates)
python -m fdmigrate run --config config.yaml --only tickets --delta

# Windows background run (survives terminal close)
Start-Process -NoNewWindow -FilePath python -ArgumentList "-m fdmigrate run --config config.yaml"
```

## Test infrastructure

```
_bulk_generate.py   — seed source with bulk synthetic tickets
_gen_1k.py          — generate 1,000 test tickets
_run_1k_test.py     — run a 1k migration test
_cleanup_target.py  — wipe test data from target
_cleanup_tickets.py — wipe test tickets from source
_reset_tickets.py   — reset ticket state in migration.db
_verify_cfmap.py    — verify custom field mapping
_verify_status.py   — verify ticket status after migration
_introspect.py      — inspect source/target account details
```

## Key design decisions
- Tickets use date-windowing (`iter_tickets`) to bypass Freshdesk's 30k list cap
- Every write is checkpointed immediately to SQLite — crash loses at most 1 record in flight
- Tickets get a `fd-migration-{source_id}` tag so re-runs never duplicate
- Conversations are posted as NOTES (not replies) so no customer emails are triggered
- `partial` status means "ticket created, conversations not done" — resume finishes them
- Attachments stream to disk, upload, then clean up — never held in RAM
