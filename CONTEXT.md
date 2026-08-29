# CONTEXT — Freshdesk → Freshdesk Migration Tool (`fdmigrate`)

_Handover pack prepared 27 Aug 2026 by SDK Infinity Technologies._
_Read this file first, then `docs/OPERATOR_QUICKSTART.md`._

---

## 0. READ THIS BEFORE YOU SPIN UP TEST INSTANCES

You are planning to test on **new Freshdesk instances**. Two things you must know up
front, or you will spend a week proving something that is already proven and still be
left with the same two open risks:

> **A brand-new Freshdesk trial CANNOT exercise the two parts of this tool that are
> actually unverified.**

| Open risk | Why a new trial can't close it |
|---|---|
| **R-A — archived-ticket ingestion** | Trials do not include the **Archive Tickets** feature. `check` will correctly report "feature not on plan", the code will run its feature-unavailable path cleanly, and you will learn nothing about the real read/create path. |
| **R-B — throughput at volume** | Trials are hard-capped at **50 API calls/min** (paid Pro is ~400/min). Any ETA you measure on a trial is ~8× pessimistic and tells you nothing about a real run. |

**What a new-instance test IS good for:** proving the happy path end-to-end on your own
data, learning the command flow, validating target-prep discipline, and reviewing the
code. That is a legitimate and worthwhile exercise — just don't mistake it for closing
R-A or R-B.

**To actually close R-A/R-B** you need a **paid Pro (or higher) account with the Archive
Tickets feature and real archived tickets.** There is no substitute.

---

## 1. What this is (30 seconds)

A standalone, single-threaded Python CLI that copies one Freshdesk account's support
history into another Freshdesk account.

**Migrates:** custom fields → agents → companies → contacts → groups → tickets
(with conversations + attachments) → canned responses → knowledge base.
That dependency order is enforced by the runner; don't reorder it.

**Design goals, in priority order:** no data loss → no duplicates → **no accidental
emails to the client's customers** → fidelity → speed.

- ~3,300 lines of Python, 19 modules, 3 runtime dependencies.
- State lives in a local SQLite checkpoint (`migration.db`), so a run is resumable.
- Dedup is two-layer: the local checkpoint **plus** `fd-migration-{id}` marker tags
  written onto the target. It survives process restarts and repeated runs.

---

## 2. Status — what is proven, what is not

**Verified 27 Aug 2026:** `54/54` unit tests pass in 0.2s.

### Proven end-to-end (live, against a 107-ticket account pair)
0 lost · 0 duplicated · 0 failed · attachments intact · crash-resume clean ·
private notes stayed private · idempotent across 12 delete/recreate cycles.
Full evidence: `docs/GO_LIVE_VALIDATION_REPORT.md` (V1–V15).

### NOT proven — be direct about this
1. **Scale.** Every validation ran against **107 tickets**. Real jobs in scope are
   52,500 and 156,000 tickets — **490× and 1,459×** the validated volume. The *logic*
   is proven; the *scale* is not.
2. **Archived ingestion (R-A).** Built, unit-tested, never run against real archived data.
3. **Throughput (R-B).** Single-threaded, ~250 calls/min observed. No concurrency.
4. **Custom-field values (R-C).** Code-covered; the test dataset had fields defined but
   all values empty.
5. **Knowledge base phase.** Code exists in `fdmigrate/phases/kb.py`, ran on the test
   pair, but has never been exercised on a substantial article set.

### Not in scope at all — the tool has no phase for these
**Business hours, SLA policies, custom ticket statuses, automations/business rules,
email templates, portal branding, DNS, webhooks, marketplace apps.**
See `fdmigrate/phases/__init__.py` — the registry has 8 entries and none of them cover
configuration. All of the above must be **recreated manually** in the target admin UI.
This is a Freshdesk API limitation, not an omission.

---

## 3. Setting up new test instances

### 3.1 You need two accounts
- **SOURCE** — only ever read. The tool never writes to it.
- **TARGET** — written to. Use a throwaway.

Get an **admin / Account Administrator API key with Global Access** for each
(Profile Settings → API Key).

### 3.2 Prepare the TARGET — mandatory, and the tool cannot do it for you
In the target admin UI, turn **OFF** for the duration of the run:

- [ ] All email notifications (**agent and requester**)
- [ ] All automations / business rules (Dispatch'r, Supervisor, Observer, Scenario)
- [ ] SLA reminders and escalations
- [ ] CSAT surveys
- [ ] "Reopen on customer response"
- [ ] Parent-child ticketing (re-enable afterwards)
- [ ] Pre-create the `created_date_custom_field` (text) if preserving original dates

> **This step is the entire zero-email guarantee.** The tool writes notes, which never
> email — but a target-side automation or notification absolutely will. If a customer
> ever receives an email during a migration, it came from target configuration, not
> from this tool.

### 3.3 Seed the source
Don't build test data by hand — **`seed/` has the scripts**, including the exact
100-ticket "worst case" dataset the tool was originally validated against. See
`seed/README.md`.

```bash
export FD_SOURCE_DOMAIN=https://yoursource.freshdesk.com
export FD_SOURCE_API_KEY=...
python -X utf8 seed/seed_edge_cases.py    # groups, KB, canned + ~30 edge cases
python -X utf8 seed/seed_pilot_100.py     # 100 tickets, attachments, conversations
python seed/seed_bulk.py 1000             # volume data for throughput testing
```

Turn the **source** account's outbound email off first — seeded requesters are fake
addresses and the bounces can trip Freshdesk's outgoing-email block.

One gap the seeder does not fill: **custom-field values** (R-C). The original
validation dataset had fields defined but all values empty, so if you can add a few
populated custom fields by hand, that closes a real untested path.

---

## 4. Running it

```bash
pip install -r requirements.txt          # requests, PyYAML, tqdm — that's all

cp config.production.yaml config.yaml    # edit the two domains + agent map
export FD_SOURCE_API_KEY="..."           # PowerShell: $env:FD_SOURCE_API_KEY = "..."
export FD_TARGET_API_KEY="..."
```

**Keys go in environment variables, never in the config file.** The config takes the
literal string `env:FD_SOURCE_API_KEY` and resolves it at run time. Please keep it
that way.

### Gated sequence — do not skip a gate
```bash
python -m fdmigrate check   --config config.yaml                    # 1. pre-flight, no writes
python -m fdmigrate run     --config config.yaml --dry-run          # 2. rehearse, zero writes
python -m fdmigrate run     --config config.yaml --only tickets --limit 50   # 3. small real sample
python -m fdmigrate verify  --config config.yaml --deep             # 4. must be 100% clean
python -m fdmigrate run     --config config.yaml                    # 5. full run (resumable)
python -m fdmigrate verify  --config config.yaml --deep --completeness ticket,contact,company
python -m fdmigrate run     --config config.yaml --delta            # 7. cutover catch-up
```

Or `python -m fdmigrate auto --config config.yaml` to run 2–6 with gates that abort on
failure. Prefer the manual sequence the first time — you learn more.

**Other commands:** `status`, `retry` (re-attempt failures), `reset --entity <name>`,
`rollback` (deletes only marker-tagged tickets; previews unless `--yes`).

### Tests — no dependencies, no network, no keys
```bash
python -m unittest discover -s tests -v      # 54 tests, ~0.2s
```

### Reading the gates
- `check` → **AGENT GAP must be 0.** Any source agent without a target counterpart must
  be created or mapped in `mapping.agents`, or their tickets lose the assignee.
- `check` → **value coverage must be clean.** Every status/priority/source/type value
  used on the source must resolve on the target, or those tickets fail mid-run.
- `verify --deep` → `verify_deep.csv` must be **100% clean** on the sample before you
  run the full job.
- `--completeness` → `MISSING = 0` per entity. This re-reads the source id-space; it is
  the real "N in = N out" proof.
- `--dry-run` over-reports contact/company "created" counts. Ticket counts are accurate.
  Known cosmetic quirk, not a bug.

---

## 5. Freshdesk API knowledge that is NOT in the official docs

**If you are re-implementing any of this elsewhere (Odoo or otherwise), this section is
the transferable value — the Python is replaceable, this knowledge cost real time.**

1. **List Tickets is hard-capped at 300 pages / 30,000 records.** Beyond that the API
   simply stops paginating. You must window by `updated_since` with
   `order_by=updated_at asc` and walk the windows. Implemented in
   `client.py → iter_windowed()`.

2. **The list payload is incomplete.** `GET /tickets` returns **no `description`, no
   `attachments`, no `requester`.** You must fetch the ticket *detail*
   (`GET /tickets/{id}?include=requester`) before building a create payload. Miss this
   and you silently create thousands of empty tickets. Highest-cost trap in the API.

3. **Archived tickets are invisible to `/tickets` entirely.** Freshdesk auto-archives
   closed tickets after ~120 days of inactivity **on every plan**. The only access we
   have found is per-id: `GET /tickets/archived/{id}` — which forces a brute-force id
   sweep. On an account with years of history this is usually the *majority* of the
   data. Probe the feature with `GET /tickets/archived/1`: a `403 require_feature`
   means the plan lacks it; `200`/`404` means archived tickets may exist.

4. **`created_at` cannot be back-dated.** Every migrated record carries the migration
   date. Original dates survive only in a custom text field (`created_date_custom_field`)
   or a provenance banner. No workaround exists on the public API.

5. **Original ticket IDs do not carry over.** The target assigns its own. For any client
   whose staff quote ticket numbers in correspondence, this must be disclosed *before*
   signature.

6. **Rate limits are per-endpoint, not just global.** The sub-limits bite first:

   | Plan | Total/min | Ticket Create/min | Tickets List/min |
   |---|---|---|---|
   | Growth | ~200 | 80 | — |
   | **Pro** | **~400** | **160** | **100** |
   | Enterprise | ~700 | 280 | — |
   | **Trial** | **50** | — | — |

   Watch the `X-RateLimit-Remaining` response header and back off proactively.

7. **A ticket with a due date fails if the target status has no SLA timer.** Fix is
   drop the due date and retry. This cost a live failure to find (V10) and appears in
   no documentation.

8. **Notes never email; replies do.** This asymmetry is the foundation of the whole
   zero-email design. `conversations_as_notes: true` converts every message to a note —
   content, privacy and portal visibility survive, but the `incoming` flag and the
   reply-vs-note distinction are flattened. That is the deliberate price of guaranteed
   silence.

9. **Account Export API** (`POST /api/v2/account/export`) is the admin-only bulk path
   Freshworks recommends above ~150k tickets. Our probes returned **HTTP 400** —
   `date_range` is a mandatory attribute and `output_format` must be from an
   undocumented allowed list. The download also requires a **web session, not an API
   key**. Treat the contract as unconfirmed until Freshworks supplies it.

Items **2, 3 and 7** do not appear in Freshworks' documentation and are only learnable
by hitting them in production.

---

## 6. Hard rules

- **Never set `force_public_notes: true`.** It would expose internal private notes to
  customers. There is no legitimate reason to enable it.
- **Never put API keys in `config.yaml`.** Use the `env:` indirection. `.gitignore`
  already excludes `config.yaml`, `*.db`, `logs/` and `reports/` — keep it that way.
- **The source is read-only.** If you find any code path that writes to source, that is
  a bug — report it immediately.
- **Keep `conversations_as_notes: true`** unless the client has explicitly accepted the
  trade-off in §5.8 and every target email path is confirmed off.
- **Re-enable** everything you disabled in §3.2 after cutover.

---

## 7. Where to look in the code

| File | Lines | What it holds |
|---|---|---|
| `fdmigrate/phases/tickets.py` | 695 | The heart. Ticket + conversation + attachment migration, archived sweep, status restore. Start here. |
| `fdmigrate/client.py` | 264 | HTTP, pagination, the 30k windowing fix, rate-limit throttling. |
| `fdmigrate/runner.py` | 339 | Phase orchestration, dependency order, gates, `auto` mode. |
| `fdmigrate/reconcile.py` | 397 | Completeness + deep field-level verification. |
| `fdmigrate/store.py` | 224 | SQLite checkpoint, id-map, dedup. |
| `fdmigrate/phases/__init__.py` | 13 | The phase registry — shows at a glance what is and isn't migrated. |

---

## 8. Docs index

| Doc | Use it for |
|---|---|
| `docs/OPERATOR_QUICKSTART.md` | **Start here.** One page, top to bottom, running a real migration. |
| `docs/GO_LIVE_VALIDATION_REPORT.md` | What was validated, how, and the residual risks (R-A…R-D). |
| `docs/PRODUCTION_RUNBOOK.md` | Full decision/input list, engineering status D0–D10. |
| `docs/MIGRATION_GUIDE.md` | Target-prep detail. |
| `docs/HANDOFF.md` | Engineering handover notes. |
| `docs/PRODUCTION_READINESS_REVIEW.md` | Independent readiness review. |
| `docs/HDM_REFERENCE.md` | Feature comparison vs. help-desk-migration.com (the spec being matched). |
| `docs/SUGGESTIONS.md` | Backlog / optional features (concurrency, inline-image rehosting, picklist mapping). |

> Note: `docs/CONTINUE_HERE.md` and the validation report reference the original test
> pair (`sdkinfinity1684` → `oskloud-supportdesk`). Those are SDK's own trial accounts,
> recorded as provenance for the validation run. No credentials are included anywhere
> in this package.

---

## 9. Questions worth raising back to SDK

1. Which Freshdesk plan will the test instances be on? (Determines whether R-A/R-B can
   be touched at all — see §0.)
2. Is the target expected to be in a specific data-centre region (EU/NA)? Region is
   fixed at account provisioning and cannot be changed afterwards.
3. Is concurrency (D9) wanted? The tool is deliberately single-threaded for safety; at
   150k+ tickets that becomes the binding constraint on the timeline.
4. Should configuration migration (SLA, business hours, automations) be added as
   phases, or stay manual? Currently manual — see §2.

---

_Package contents: 45 files — source, 54 unit tests, 9 docs, 2 config templates,
Dockerfile. No credentials, no state files, no logs, no client data._
