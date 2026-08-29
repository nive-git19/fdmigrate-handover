# Test instances

The shared trial pair for migration testing and validation.

| | Source | Target |
|---|---|---|
| Domain | `sdkinfinity8689.freshdesk.com` | `sdkinfinity-help.freshdesk.com` |
| Admin | `/a/admin` | `/a/admin` |
| Region | **US** | **EU** |
| Role | **Read only** — never written to | **Written to** |
| Plan | Trial | Trial |

## Credentials

**Not in this repository, and they never will be.** API keys and admin passwords
are requested from SDK directly.

The tool reads keys from environment variables — they are never written to a
config file:

```bash
cp config.testpair.yaml config.yaml     # domains already filled in

export FD_SOURCE_API_KEY=...            # ask SDK
export FD_TARGET_API_KEY=...

python -m fdmigrate check --config config.yaml
```

If `check` reports both accounts OK, you're wired up correctly.

---

## Measured on these accounts — read this before planning your tests

Both instances were probed on **27 Aug** and re-probed on **28 Aug 2026**. Three
findings shape what this pair can prove — read the first one before you run
anything.

### 0. The target is NOT empty, and its data has no migration markers

```
TARGET (EU)  30 tickets (#1-#30), 6 companies, 20 contacts
SOURCE (US)  27 pre-existing tickets (#20-#46) that mirror them,
             plus the 10-ticket demo set (#47-#56)
tickets carrying an fd-migration-* marker, either side: 0
```

The two accounts were populated from each other on 28 Aug by something that
was not this tool, or the markers would be there.

`fdmigrate` dedups on two layers: the local SQLite checkpoint and `fd-migration-*`
marker tags on the target. Neither exists for that data, so **the tool cannot
recognise any of those 27 tickets as already migrated, and a first run will
create 27 duplicates.**

That is not a defect — it is what happens when a tool migrates into a target
another process has written to without shared bookkeeping. Resolve it before
testing:

| Option | Command |
|---|---|
| **Wipe the target** *(recommended)* | `python seed/cleanup_tickets.py --yes` |
| Fresh third trial as the target | sign up, update `config.yaml` |
| Proceed and accept duplicates | only to demonstrate the failure mode |

There is also a stale test case worth knowing about: source ticket `[RMATT-003]
Attachment on a note` **carries no attachment**, on either side. It reports PASS
because nothing is being compared. Re-seed before trusting attachment results.

### 1. Rate limit: 50 calls/min on both

```
x-ratelimit-total: 50.0
```

Paid Pro is ~400/min. Every timing you take here is roughly **8× slower than
production**, so this pair cannot produce a meaningful ETA. **Risk R-B
(throughput at volume) cannot be closed on these accounts.**

### 2. Archive Tickets: not on the plan, both sides

```
GET /tickets/archived/1
HTTP 403  {"code":"require_feature",
           "message":"The Archive Tickets feature(s) is/are not supported in your plan..."}
```

Freshdesk auto-archives closed tickets after ~120 days on paid plans, and archived
tickets are invisible to the normal `/tickets` endpoint. On a real client account
they are often the *majority* of the data.

Here, `check` will correctly report "feature not available" and the ingestion code
will run its feature-unavailable path cleanly — telling you nothing about the real
read/create path. **Risk R-A (archived ingestion) cannot be closed on these
accounts either.**

Both risks are documented in `docs/GO_LIVE_VALIDATION_REPORT.md` §4. Closing them
needs a **paid Pro account with the Archive feature and real archived tickets**.
There is no substitute.

### What this pair IS good for

- End-to-end happy path on data you control
- Field-level fidelity (`verify --deep`)
- Completeness reconciliation — "N in = N out"
- Idempotency: re-run and confirm zero duplicates
- Crash-resume: Ctrl-C mid-run, re-run, confirm no re-creation
- Attachment handling, conversation order, private-note privacy
- Target-prep discipline and the gated command flow
- Reviewing the code against real behaviour

That is a genuinely worthwhile test pass. Just don't mistake it for a scale
certification.

**Before you plan a run, read two documents:** `docs/TEST_DATASET_SPEC.md` (what
the data must contain to prove anything — measured, three of the eight phases
currently have zero objects to migrate) and `docs/PRE_MIGRATION_TEST_CASES.md`
(the numbered runbook and sign-off sheet).

---

## Direction note

This pair runs **US → EU**, matching the direction of the production engagement
it rehearses for.

That matters more than it looks. Attachments are served from region-specific
hosts (`freshdeskusercontent.com` for US, `freshdeskusercontent-euc.com` for EU),
so the pair exercises each host in the same role it will hold in production —
reading from US, writing to EU.

---

## Ground rules

1. **Source is read-only.** The tool never writes to it. If you find a code path
   that does, that's a bug — report it.
2. **Prepare the target before any run** — email notifications, automations, SLA
   escalations and CSAT **off**. See `CONTEXT.md` §3.2. This is what stops test
   traffic emailing real addresses.
3. **Don't seed until asked.** Scripts are in `seed/`, ready to go, but SDK will
   confirm the dataset first.
4. **`seed/cleanup_tickets.py` deletes tickets.** It previews by default and needs
   `--yes`; it will not touch the source without `--include-source`. Read the
   domains it prints before confirming.
5. Both are throwaway trials — but they're shared, so say so before you wipe them.
