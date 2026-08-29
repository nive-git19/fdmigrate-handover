# Pre-migration test cases

The runbook for validating `fdmigrate` on the shared trial pair. Work top to
bottom. Every test states what it *proves* — if you can't say what a green
result rules out, it isn't a test.

Record results in the sign-off table at the end (§8).

```
SOURCE (read-only)  https://sdkinfinity-help.freshdesk.com     EU
TARGET (write)      https://sdkinfinity8689.freshdesk.com      US
```

---

## 0. STOP — read this before you run anything

Measured on the pair, 28 Aug 2026:

**The target is not empty.** It holds 27 tickets, 6 companies and 20 contacts
that mirror the source. Something already migrated into it.

**Those 27 tickets carry no `fd-migration-*` marker tag.** Our tool's dedup is
built on two layers — the local SQLite checkpoint and those marker tags on the
target. Neither exists for this data, so `fdmigrate` will not recognise a single
one of those tickets as already migrated.

> **Run the tool against the pair as it stands today and you get 27 duplicates
> on the first run.** Not a bug — the tool is being asked to migrate into a
> target somebody else has already written to, with no shared bookkeeping.

Resolve it before test P-1. Pick one:

| Option | Command | When |
|---|---|---|
| **A — wipe the target** *(recommended)* | `python seed/cleanup_tickets.py --yes` | clean baseline; counts mean what they say |
| B — fresh third trial as target | sign up, update `config.yaml` | keeps the current target as a reference copy |
| C — migrate anyway, accept duplicates | — | only to *demonstrate* the failure mode |

`cleanup_tickets.py` previews by default and needs `--yes`. It will not touch
the source unless you pass `--include-source`. **Read the domains it prints
before confirming.**

Two limits are fixed and no test will change them: both accounts are capped at
**50 API calls/min**, and both return `403 require_feature` for archived
tickets. Risks **R-A** (archived ingestion) and **R-B** (throughput) cannot be
closed here. Don't design tests that pretend otherwise.

---

## 1. Setup

```bash
cp config.testpair.yaml config.yaml
export FD_SOURCE_API_KEY=...          # from SDK, out of band
export FD_TARGET_API_KEY=...
python -m unittest discover -s tests  # expect: 54 tests, OK
```

Then seed the source — see `TEST_DATASET_SPEC.md` §4. Without that data most of
what follows is an empty loop: three of the eight phases currently have zero
objects to migrate.

---

## 2. P-series — pre-flight (nothing has been written yet)

| ID | Proves | Do | Pass |
|---|---|---|---|
| **P-1** | Auth + reachability both sides | `python -m fdmigrate check` | both accounts report OK |
| **P-2** | Value coverage: every source status/priority/source/type resolves on the target | `check` output | no unresolved values. E16/E17/E18 make this real — before seeding, 4 statuses; after, 7 |
| **P-3** | Agent-gap detection | `check` | source agents with no target counterpart are **listed before the run**, not discovered during it |
| **P-4** | Custom-field presence | `check` | `cf_reference_number` found on both |
| **P-5** | Pre-flight actually fails when it should | rename a target status label, re-run `check` | **must report the gap.** A pre-flight that never fails is decoration — negative-test it once |
| **P-6** | Archived detection degrades cleanly | `check` | reports *feature not available*, does not crash. Detection only — ingestion stays unproven (R-A) |
| **P-7** | Target is prepared | target admin UI | email notifications, automations, SLA escalation, CSAT **all OFF**. This is what makes the run silent — the tool cannot enforce it |

**P-7 is the one people skip and the one that emails real customers.** Do it, and
screenshot it.

---

## 3. M-series — the migration run

| ID | Proves | Do | Pass |
|---|---|---|---|
| **M-1** | Phase order holds | `python -m fdmigrate migrate` | phases run `custom_fields → agents → companies → contacts → groups → tickets → canned_responses → knowledge_base` |
| **M-2** | Groups actually migrate | after E19 seeding | 3 groups on target, tickets land in the right one. *Zero groups exist today — this path has never executed* |
| **M-3** | Canned responses migrate | — | 2 folders, 4 responses. *Never executed today* |
| **M-4** | KB migrates incl. **drafts** | — | 2 categories, 3 folders, 9 articles. Confirm the draft article arrived — tools that read only published silently drop them |
| **M-5** | Throughput is recorded | note wall-clock and ticket count | a number, logged. **Do not extrapolate it** — 50/min is ~8× slower than Pro |
| **M-6** | Failures are surfaced, not swallowed | `reports/failures.csv` | empty, or every row explained |

---

## 4. V-series — verification

| ID | Proves | Do | Pass |
|---|---|---|---|
| **V-1** | Completeness, N in = N out | `verify --deep` | 0 missing across tickets, contacts, companies |
| **V-2** | Field-level fidelity | `verify --deep` | 0 mismatch on the sample |
| **V-3** | Unicode survived | open E01, E02 on the target | emoji, CJK, Greek, RTL all intact. **Compare rendered text, not `len()`** |
| **V-4** | Long threads complete | E03 | **30** conversations, not 30-minus-something. This is where a `per_page` default bites |
| **V-5** | Custom-field values carried | E05 | value present and equal. **Closes risk R-C** |
| **V-6** | Attachments byte-identical | pilot dataset | compare `size` and re-download. The two files on the pair today are 70 B and 60 B — seed real ones first |
| **V-7** | Private stays private | pilot + E24 | every private note still `private: true`. A single leak fails the whole run |
| **V-8** | Non-default statuses | E16, E17 | *Waiting on Customer* / *Waiting on Third Party*, not silently coerced to Open |
| **V-9** | Large body intact | E09 | ~90 KB description not truncated |
| **V-10** | Known drops are known | E21 time entries | absent on the target **and** named in the report. An undocumented drop is a defect; a documented one is scope |

---

## 5. X-series — records that must NOT arrive

The reconciliation CSV **cannot catch these** — it counts what came across, not
what shouldn't have. Assert them explicitly.

| ID | Proves | Pass |
|---|---|---|
| **X-1** | Deleted ticket (E22) did not migrate | absent from target |
| **X-2** | Spam ticket (E23) did not migrate | absent from target |
| **X-3** | No duplicate contacts from aliases (E13) | one contact, not three |
| **X-4** | Source is untouched | source ticket count, tags and `updated_at` unchanged end to end. Any write to the source is a bug — report it |

---

## 6. R-series — resilience

| ID | Proves | Do | Pass |
|---|---|---|---|
| **R-1** | Idempotency | re-run `migrate` unchanged | everything skipped, **0 created**, 0 duplicate markers |
| **R-2** | Crash-resume | `Ctrl-C` mid-run, re-run | resumes from checkpoint, no re-creation |
| **R-3** | Marker integrity after churn | delete 2 target tickets, re-run | exactly 2 recreated |
| **R-4** | Rollback previews first | `python -m fdmigrate rollback` | **prints a preview, deletes nothing** |
| **R-5** | Rollback executes and is scoped | `rollback --yes` | deletes only marker-tagged tickets. Contacts, companies and agents **survive** — they are matched client data, never tool-created |

**R-5 has never been executed.** The code path is written and reviewed but has
not run against a live target. Run it last, on the throwaway target, and record
the result — it is one of two genuinely unexercised paths in the tool.

---

## 7. E-series — the real-mailbox loop

The validation report's Limitation #5 says the zero-email guarantee is
*"mechanism-verified, not inbox-verified."* A real mailbox is the missing
oracle. Seed E24 with `FD_CANARY_EMAIL` first.

### E-1 — zero-email during migration *(the important one)*

1. Confirm P-7: target notifications, automations, SLA escalation, CSAT all OFF.
2. Note the canary inbox time. Leave it open.
3. Run the full migration.
4. Inspect the inbox — **including Spam and Promotions**.

**Pass: zero new mail.** Any message means the guarantee failed. This is the
single most valuable test in the document: it is the one that, if it fails
against a real client, ends the engagement.

### E-2 — notes never email, by construction

With notifications back **ON** on the target, add a private note and a public
note to a migrated canary ticket via the API.

**Pass: neither emails the canary.** This is the mechanism behind
`conversations_as_notes: true` — confirm it directly rather than trusting it.

### E-3 — post-migration continuity *(never tested before)*

On a **migrated** canary ticket, with notifications ON:

1. Agent posts a real `/reply`.
2. Canary mailbox receives it.
3. **Reply from the mailbox.**
4. Check the target.

**Pass:** the reply threads onto the *migrated* ticket as an incoming
conversation. **Fail:** it opens a new ticket — meaning migrated tickets are
inert archives, not live tickets the customer can continue.

Nothing in the reconciliation reports tells you this. It is the difference
between "the data arrived" and "the helpdesk works."

### E-4 — genuine inbound mail (highest fidelity, free)

Send a real email from any mailbox to
`support@sdkinfinity-help.freshdesk.com`, reply once from the same mailbox, then
migrate that ticket.

**Pass:** real headers, quoted history and threading survive. This is the only
case with authentic `Message-ID` chains — API-seeded conversations cannot
reproduce them.

---

## 8. On paying for the migration tool subscription

Worth doing, but **run the free demo first** — it very likely answers the
question for nothing.

- HDM's **Demo migration is free** and writes a real sample into the real
  target. That is already proven: migration `3BA32AEEDD6126`, 6 tickets, 0
  failed (`HDM_REFERENCE.md`).
- Demo supports **"Select records for Demo (by ID)"** — point it at the exact
  edge cases, E01/E02/E03/E05/E08/E09. Their hardest 20 records, free.
- It produces **downloadable reports** — the side-by-side artefact you want.

**If you do pay, don't run both tools into the same target at once.** They are
distinguishable (HDM tags `imported`, we tag `fd-migration-{id}`), but every
count becomes ambiguous and reconciliation stops meaning anything. Run HDM
first, capture the reports, `cleanup_tickets.py --yes`, then run ours.

Set expectations on the diff. `HDM_REFERENCE.md` §Gap Analysis already lists
what HDM does that we don't — create custom fields on the target, per-value
picklist mapping, time logs, mapped created/closed dates, rollback of a
migration. **A paid comparison will mostly re-confirm that known list.** Pay if
you want the ticket-level side-by-side as a client-facing artefact; don't pay
expecting to discover new gaps.

---

## 9. Sign-off

| ID | Test | Result | Evidence | Notes |
|---|---|---|---|---|
| P-1 | Connectivity | | | |
| P-2 | Value coverage | | | |
| P-3 | Agent gap | | | |
| P-4 | Custom fields present | | | |
| P-5 | Pre-flight negative test | | | |
| P-6 | Archived detection | | | R-A stays open |
| P-7 | Target prepared | | | screenshot |
| M-1 | Phase order | | | |
| M-2 | Groups | | | first ever run |
| M-3 | Canned responses | | | first ever run |
| M-4 | KB incl. drafts | | | first ever run |
| M-5 | Throughput recorded | | | do not extrapolate |
| M-6 | Failures surfaced | | | |
| V-1 | Completeness | | | |
| V-2 | Field fidelity | | | |
| V-3 | Unicode | | | |
| V-4 | 30-message thread | | | |
| V-5 | Custom-field values | | | closes R-C |
| V-6 | Attachments | | | |
| V-7 | Private stays private | | | |
| V-8 | Non-default statuses | | | |
| V-9 | Large body | | | |
| V-10 | Known drops documented | | | |
| X-1 | Deleted excluded | | | |
| X-2 | Spam excluded | | | |
| X-3 | No alias duplicates | | | |
| X-4 | Source untouched | | | |
| R-1 | Idempotency | | | |
| R-2 | Crash-resume | | | |
| R-3 | Marker integrity | | | |
| R-4 | Rollback preview | | | |
| R-5 | Rollback execute | | | first ever run |
| E-1 | **Zero-email during migration** | | | inbox screenshot |
| E-2 | Notes never email | | | |
| E-3 | Post-migration continuity | | | never tested before |
| E-4 | Real inbound mail | | | |

**Still open after a clean sweep of this document:** R-A (archived ingestion)
and R-B (throughput at volume). Both need a paid Pro account with real archived
history. There is no substitute, and no amount of green on this pair is one.
