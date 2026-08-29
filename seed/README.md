# Seed & test-harness scripts

Helper scripts for **populating a throwaway Freshdesk account with test data** so
you have something meaningful to migrate, plus the cleanup and inspection tools
that go with them.

> ⚠️ **These write to and delete from live Freshdesk accounts.**
> Point them at throwaway test instances only. None of them checks whether an
> account is a trial — that judgement is yours.

Run everything from the **repository root**, not from inside `seed/`, so the
`fdmigrate` package is importable:

```bash
python seed/seed_pilot_100.py        # correct
cd seed && python seed_pilot_100.py  # will fail on import
```

---

## Before you seed: turn off the source account's outbound email

Seeded tickets use fake requester addresses. Their bounces can trip Freshdesk's
outgoing-email block on the account doing the sending.

In the **source** admin UI, disable email notifications before running any seeder.
This is the same discipline the migration itself needs on the target
(`docs/MIGRATION_GUIDE.md`), applied to the other side.

---

## `seed_pilot_100.py` — the "worst case" 100-ticket dataset

The dataset the tool was originally validated against. Deliberately awkward, so it
exercises the paths that break.

```bash
export FD_SOURCE_DOMAIN=https://yourtrial.freshdesk.com
export FD_SOURCE_API_KEY=...
python -X utf8 seed/seed_pilot_100.py
```

Creates 100 tickets covering:

| Dimension | Coverage |
|---|---|
| Attachments | PDF, PNG, DOCX, MP3, CSV — on descriptions **and** on replies; multi-attachment tickets; one ~6 MB file for the oversize cap |
| Conversations | Customer messages as **incoming** notes, agent replies as real `/reply` posts, internal notes private |
| Status | Even 25/25/25/25 spread across Open / Pending / Resolved / Closed |
| Assignment | Every ticket assigned to a rotating agent; most also to a group |
| Requesters | Mix of existing contacts and auto-created ones, CCs on some |

Notes:

- **Fixtures generate themselves** into `_pilot_files/` on first run — nothing
  binary is stored in this repo.
- `random.seed(42)`, so the dataset is reproducible.
- Everything is tagged **`zzz-pilot-100`** for cleanup.
- Writes `_pilot_manifest.json` (ticket ids + expected counts) for verification.
  Both that file and `_pilot_files/` are gitignored — they're run artefacts and
  contain real ids from *your* account.

**The private note matters.** It's how you prove the migration keeps internal
notes internal. Check it after migrating, every time.

---

## `seed_edge_cases.py` — the cases that actually break migration tools

`seed_pilot_100.py` covers volume and attachments. This one covers the nasty
half, plus the three phases the pilot dataset never exercises at all.

```bash
export FD_SOURCE_DOMAIN=https://yourtrial.freshdesk.com
export FD_SOURCE_API_KEY=...
export FD_CANARY_EMAIL=you@gmail.com     # optional, enables the E24 canary
python -X utf8 seed/seed_edge_cases.py
```

Creates ~30 tickets tagged **`zzz-edge`**, plus config:

| Dimension | Coverage |
|---|---|
| Config | 3 **groups**, 2 KB categories / 3 folders / 9 articles (one **draft**), 2 canned folders / 4 responses — all three phases currently migrate **zero** objects without this |
| Encoding | emoji, CJK, Greek, RTL Arabic/Hebrew, zero-width space, HTML entities |
| Structure | a **30-message** thread, mixed incoming/outgoing/private |
| Identity | unicode name, no-name contact, `other_emails` aliases, phone-only contact, requester who is also an agent, fully unassigned |
| Values | **custom-field values** (the open risk R-C), non-default statuses, all 6 sources, due dates, time entries |
| Negative | a **deleted** ticket and a **spam** ticket that must *not* migrate |
| Canary | 5 tickets on a real mailbox you own — the oracle for the zero-email guarantee |

Writes `_edge_manifest.json` with every id created; that file drives the
post-migration assertions. Anything unsupported on a trial plan is printed and
skipped rather than fatal — **record what skipped, that list is itself a finding.**

Each case maps to a numbered entry in `docs/TEST_DATASET_SPEC.md`, and the tests
that consume them are `docs/PRE_MIGRATION_TEST_CASES.md`.

---

## `seed_deep.py` — the fidelity dataset (`zzz-deep`, ~15 tickets)

The dataset that decides whether a migration tool is actually good. Run
`seed_demo_10.py` first — this one reuses its groups and contacts.

```bash
python -X utf8 seed/seed_deep.py
```

| Covers | Detail |
|---|---|
| Cross-agent work | handoff mid-thread with the group changing too |
| Email realism | signature blocks, a quoted chain nested 3 deep, a legal disclaimer |
| Attachments | description, customer reply, agent reply, **private note**, the same filename twice, a unicode filename, and a 1.5 MB file |
| Threads | a **40-message** thread — past every default page size |
| Journeys | Open → Pending → Waiting on Customer → Waiting on Third Party → Resolved → Closed |
| Values | custom fields populated, time logged by two agents, multi-address CC |
| Edges | zero-conversation ticket, agent as requester, forwarded mail, inline `data:` image |

Measured after seeding: **69 conversations, 27 incoming, 12 private, 10
attachments, 1.59 MB**. Per-case expectations are in
`docs/MIGRATION_FIDELITY_SPEC.md` §4.

Two things it will tell you about the platform rather than the tool:

- **Cross-agent attribution is refused.** Posting a note as another agent
  returns `403 invalid_user`, even for an Account Administrator with Global
  Access. The seeder falls back to the key owner and reports each downgrade.
- **`data:` URI images are stripped** by Freshdesk on the way in.

Both are documented in `docs/MIGRATION_FIDELITY_SPEC.md` §2.

Fixtures (PNG/PDF/DOCX/CSV/LOG/ZIP) are generated by `seed/_fixtures.py` on
first run — nothing binary lives in this repo.

---

## `seed_bulk.py` — volume data for throughput testing

```bash
python seed/seed_bulk.py          # 1000 tickets (default)
python seed/seed_bulk.py 5000     # or pass a count
```

One private note each, **no attachments** — built for speed, not fidelity. Varies
status, priority, type and source; roughly half assigned and grouped; every fifth
carries unicode/special characters to shake out encoding bugs.

Reads its domains from `config.yaml`, like the migration tool itself.

This is the script for measuring throughput — but read
`docs/GO_LIVE_VALIDATION_REPORT.md` §4 (R-B) first: **a trial account is capped at
50 API calls/min against Pro's ~400**, so any timing you take on a trial is roughly
8× pessimistic and won't tell you what a real run costs.

---

## `introspect.py` — pre-migration safety check

```bash
python seed/introspect.py
```

Read-only. Verifies access to both accounts, compares ticket fields (especially
**custom** fields) source → target, and lists agents with an auto-match by email.

Answers the question that causes most mid-run failures: *do the two accounts
actually line up before we start?* Run it alongside `python -m fdmigrate check`.

---

## `cleanup_tickets.py` — reset between runs

```bash
python seed/cleanup_tickets.py                          # preview, deletes nothing
python seed/cleanup_tickets.py --yes                    # delete TARGET tickets
python seed/cleanup_tickets.py --yes --include-source   # also delete SOURCE tickets
```

Deletes tickets and purges the local ticket/conversation state from
`migration.db`, so the next migration doesn't silently skip everything as
"already done".

**Tickets only** — agents, contacts, groups, companies, canned responses and KB
are untouched. Freshdesk soft-deletes, so they land in Trash. Every id is logged.

Three deliberate guards, because this is the most destructive script here:

1. **Preview by default.** Without `--yes` it counts and reports, and deletes nothing.
2. **Source is opt-in.** `--yes` alone touches only the target. Deleting source
   tickets destroys the very data you're migrating, so it needs `--include-source`.
3. **It prints both domains** from your config before doing anything. Read them.

For undoing a *migration* specifically, prefer the tool's own command — it only
ever removes tickets it created, identified by their marker tag:

```bash
python -m fdmigrate rollback --config config.yaml          # preview
python -m fdmigrate rollback --config config.yaml --yes    # delete
```

---

## Suggested first run

```bash
# 1. source account email OFF in the admin UI

export FD_SOURCE_DOMAIN=https://yoursource.freshdesk.com
export FD_SOURCE_API_KEY=...
export FD_TARGET_API_KEY=...

python seed/seed_edge_cases.py    # 2. groups, KB, canned + the edge cases
python seed/seed_pilot_100.py     # 3. volume, attachments, conversations
python seed/introspect.py         # 4. do the accounts line up?
python -m fdmigrate check --config config.yaml
python -m fdmigrate run   --config config.yaml --dry-run
python -m fdmigrate run   --config config.yaml --only tickets --limit 20
python -m fdmigrate verify --config config.yaml --deep
```

If `verify --deep` isn't clean at 20 tickets, stop and read the mismatched fields
before going further. That gate exists to fail cheaply.
