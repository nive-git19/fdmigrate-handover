# Enhancement Suggestions
# Freshdesk Migration Tool — Road to Production Grade

Last updated: 2026-07-02

## STATUS UPDATE 2026-07-02
- **S-00 (new, was the worst bug of all): tickets migrated with EMPTY bodies** —
  the `/tickets` LIST payload omits `description`/`attachments`/`requester`.
  FIXED: the create path now fetches `GET /tickets/{id}?include=requester` and
  builds the payload from the detail. Live-verified on 7 re-migrated tickets
  (bodies, 3/3 attachments, requesters all intact).
- S-01 DONE (generic `iter_windowed()` + `iter_contacts()`; `contacts.updated_since` config)
- S-02 MITIGATED — `/companies` does not support `updated_since`, so windowing is
  impossible; `paginate()` now logs a loud TRUNCATED warning and `check` shows
  `30000+ (30k list cap reached)`
- S-03 DONE (reconciliation.csv = full idmap accounting; API sample moved to
  ticket_spot_check.csv with `run.spot_check_sample`)
- S-04 DONE (AGENT GAP warning in `check`, honours `mapping.agents`)
- S-05 / S-06 DONE (Heartbeat in contacts + companies)
- S-11 DONE as `run --delta` (cursor `tickets_last_run_started_at`; live-verified:
  new source note synced, no duplicates)
- Still open: S-07, S-08, S-09, S-10, S-12, S-13, S-14, S-15

---

## Section 1 — Bugs to Fix Before Any Production Run

### S-01 | Contacts 30,000-record cap (BLOCKER)
**File:** `fdmigrate/phases/contacts.py` line 68
**Problem:** `ctx.src.paginate("/contacts")` stops at 300 pages × 100 records = 30,000 contacts.
A client with 206,007 contacts will silently lose 176,007 records.
**Fix:** Add `iter_contacts()` to `fdmigrate/client.py` using the same date-windowing
strategy as `iter_tickets()`. Freshdesk `/contacts` supports `updated_since`,
`order_by=updated_at`, `order_type=asc`. Add `contacts_updated_since` to `Config`
and replace the `paginate` call in `contacts.py` with the new iterator.

---

### S-02 | Companies 30,000-record cap (same bug)
**File:** `fdmigrate/phases/companies.py` lines 36 and 40
**Problem:** Both `paginate("/companies")` calls (source fetch and target prefetch) cap at 30k.
**Fix:** Same approach as S-01. Add `iter_companies()` or a generic windowed iterator.
Less urgent than contacts (companies rarely exceed 30k) but fix for correctness.

---

### S-03 | Reconciliation report is incomplete
**File:** `fdmigrate/reconcile.py`
**Problem:** After a 1,000-ticket test run the reconciliation CSV only produced 7 rows,
not 1,000. The report is not scanning all migrated records.
**Fix:** Read `reconcile.py`, identify why it samples instead of scanning all idmap rows,
and fix it to emit one row per migrated ticket with full subject/conversation-count comparison.

---

### S-04 | No agent pre-validation before migration starts
**File:** `fdmigrate/runner.py` — `check()` function
**Problem:** Agent mismatches only appear as entries in `failures.csv` mid-migration,
after tickets have already been processed without an assignee.
**Fix:** Extend `check` to fetch all source agent emails and all target agent emails,
diff them, and print a clear gap list. Agents in the gap must be manually created
in the target account before running the migration.

---

## Section 2 — High Value Improvements

### S-05 | Progress heartbeat in contacts phase
**File:** `fdmigrate/phases/contacts.py`
**Problem:** Contacts only logs every `save_every=20` records. No throughput, no elapsed
time. On a 206k contact run the operator has no idea how fast it is going.
**Fix:** Add a `Heartbeat` instance (same pattern as `phases/base.py:32`) and call
`hb.beat(i, created=created, updated=updated, failed=failed)` inside the loop.

---

### S-06 | Progress heartbeat in companies phase
**File:** `fdmigrate/phases/companies.py`
**Problem:** Companies has no progress logging at all.
**Fix:** Same as S-05.

---

### S-07 | ETA in Heartbeat output
**File:** `fdmigrate/phases/base.py` — `Heartbeat.beat()`
**Problem:** Heartbeat reports rate and elapsed but not estimated time remaining.
For a 4-day run the operator needs to see "~3h 20m remaining".
**Fix:** Add optional `total` param to `Heartbeat.__init__`. When `total` is known,
compute `remaining = (total - processed) / rate` and include it in the log line.

---

### S-08 | Cache the target marker prefetch
**File:** `fdmigrate/phases/tickets.py` — `_prefetch_target_markers()`
**Problem:** Every run scans ALL target tickets to build the marker map. On 156k tickets
this takes ~26 minutes before a single source ticket is processed. On resume runs
this scan repeats from scratch even though nothing changed.
**Fix:** After the scan, store the marker map in the SQLite `cursors` table as a
JSON blob keyed by `"marker_cache"`. On the next run, load from cache if the cursor
exists. Add a `--refresh-markers` CLI flag to force a fresh scan when needed.

---

### S-09 | Dry-run mode
**File:** `fdmigrate/__main__.py`, `fdmigrate/runner.py`
**Problem:** There is no way to validate config, count records, and check field mappings
without writing anything to the target. Clients want a preview before committing.
**Fix:** Add `--dry-run` flag to the `run` command. In dry-run mode, fetch source
records, validate payloads, and log what WOULD happen — but skip all POST/PUT calls
to the target.

---

### S-10 | End-of-run summary with total elapsed time
**File:** `fdmigrate/runner.py` — `run_migration()`
**Problem:** The final log line does not include total runtime.
**Fix:** Record `t0 = time.time()` at the start of `run_migration()` and log a single
summary line at the end:
`Migration complete in 4h 32m | tickets=156122 contacts=206007 failed=3`

---

### S-11 | Delta migration mode
**Problem:** A migration of 156k tickets takes 4+ days. During that time the client's
team is still working in the old Freshdesk — new tickets arrive, existing tickets
are updated. Without a delta pass, those changes never reach the target.
**Fix:** Add `--mode delta` to the `run` command. At the start of each full run, store
`run_started_at` in the SQLite `cursors` table. A delta run re-runs the ticket phase
with `updated_since = run_started_at` from the cursor, syncing only what changed
since the last run. This closes the most significant gap vs commercial tools.

---

## Section 3 — Reliability Polish

### S-12 | Attachment URL expiry re-fetch
**File:** `fdmigrate/phases/tickets.py` — `_download_attachments()`
**Problem:** Freshdesk S3 pre-signed attachment URLs expire in ~30 minutes. On a slow
run with large files, a URL fetched early in the day may be expired by the time
`download_to()` is called. Currently the tool records it as "unavailable".
**Fix:** On a 403 response from `download_to()`, re-fetch the parent ticket or
conversation from the source API to get a fresh URL and retry the download once.

---

### S-13 | Knowledge base article attachments
**File:** `fdmigrate/phases/kb.py`
**Problem:** `kb.py` migrates article text but not embedded images or file attachments
within articles. For clients with image-heavy documentation this is a visible gap.
**Fix:** After creating each article, fetch its attachment list and upload them to
the target article using the same multipart pattern as ticket attachments.

---

### S-14 | Reset a single entity without manual SQL
**File:** `fdmigrate/__main__.py`, `fdmigrate/store.py`
**Problem:** To re-run just the contacts phase from scratch you must manually delete
rows from the SQLite `idmap` table. There is no CLI command for this.
**Fix:** Add a `reset` subcommand: `python -m fdmigrate reset --entity contacts --config config.yaml`
that deletes all `idmap` rows for that entity and any related `conversations`/`attachments` rows.

---

### S-15 | Windows long-run guidance
**File:** `config.example.yaml`, `README.md`
**Problem:** A 4-day migration run on Windows will fail if the terminal is closed.
The existing `__main__.py` docstring mentions `Start-Process` but it is not in
the user-facing docs.
**Fix:** Add a clearly labelled section to README.md:
```
# Running as a background process (Windows)
Start-Process -NoNewWindow -FilePath python `
  -ArgumentList "-m fdmigrate run --config config.yaml" `
  -RedirectStandardOutput ".\logs\run.out"
```

---

## Section 4 — Comparison with Commercial Tools

| Capability | Commercial tools | This tool (after fixes) |
|---|---|---|
| Tickets + conversations + attachments | Yes | Yes |
| Contacts + custom fields | Yes | Yes (after S-01) |
| Companies, groups, agents | Yes | Yes |
| Knowledge base | Yes | Yes |
| Custom field mapping | Yes (via UI) | Yes (via config) |
| Handles 100k+ records | Yes | Yes |
| Crash-safe resume | Yes | Yes |
| No duplicates on re-run | Yes | Yes |
| Failure logging + manifest | Yes | Yes |
| Reconciliation report | Yes | Yes (after S-03) |
| **Delta migration** | **Yes** | **Yes (`run --delta`, added 2026-07-02)** |
| **Original timestamps preserved** | Partial* | Partial* |
| **Web UI / dashboard** | Yes | No (CLI only) |
| **Parallel processing** | Yes (faster) | No (single-threaded) |
| **Per-ticket cost** | $1,800 for 156k | None |
| **Full transparency / auditability** | No | Yes |
| **Filter by status / group / date** | Limited | Yes (config) |
| **You own the code** | No | Yes |

*Freshdesk API rejects back-dated `created_at`. Both commercial tools and this tool
work around this with a provenance banner. Commercial tools with Freshdesk partner
status may have access to a private bulk-import API that allows back-dating.

---

## Priority Order for Implementation

1. S-01 Contacts windowing
2. S-02 Companies windowing
3. S-03 Reconciliation fix
4. S-04 Agent pre-validation
5. S-05 Contacts heartbeat
6. S-06 Companies heartbeat
7. S-07 ETA in heartbeat
8. S-08 Cache marker prefetch
9. S-09 Dry-run mode
10. S-10 End-of-run summary
11. S-11 Delta migration mode ← closes biggest gap vs commercial tools
12. S-12 Attachment URL expiry
13. S-13 KB article attachments
14. S-14 Reset entity command
15. S-15 Windows run guidance
