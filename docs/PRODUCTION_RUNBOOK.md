# Production Runbook — What Is Required To Go Live
_Companion to PRODUCTION_READINESS_REVIEW.md. This is the checklist that turns the
tool from "works on trials" into "trusted for a real client migration."_

There are two kinds of requirement: **things only you can decide or provide**
(Parts A–C) and **engineering work to close the gaps** (Part D, which I do once
A–C are settled). Part E is the go/no-go gate for each client.

---

## PART A — Decisions only you can make (settle these first)

| # | Decision | Why it matters | Default I recommend |
|---|----------|----------------|---------------------|
| A1 | **Conversation mode:** as-is (`conversations_as_notes: false`) vs notes-only (`true`) | Defines fidelity. As-is = customer replies look like real inbound messages + agent replies are real replies. Notes-only = everything looks like an agent note (this is what caused your "replies became notes" symptom). As-is REQUIRES notifications off. | **As-is**, with notifications off (C-block) |
| A2 | **Timestamp handling:** provenance banner on/off + `created_date_custom_field` | The API cannot back-date. Without the banner, every migrated ticket/reply shows the migration date, and original per-message times are lost. | Banner **on**; also stamp created date into a custom field |
| A3 | **Unmatched agents:** leave tickets unassigned vs assign to a default agent | The tool is match-by-email only today. HDM offers a default-agent fallback. | Decide per client; add default-agent if they care |
| A4 | **Rollback expectation:** do clients need a "undo my migration" button? | Buildable but needs an `origin` column first (see review §8). If yes, it's real work. | Build it if you sell this as a service |
| A5 | **Verification bar:** what counts as "migration certified"? | Today's spot-check is subject + conversation count only — too shallow to sign off a client. | Field-level diff on a sample (D3) |

## PART B — Access & inputs only you can provide (per client)

- [ ] **Source API key** — an admin/Account-Administrator agent key with Global Access scope (read).
- [ ] **Target API key** — same, on the destination account (write).
- [ ] **Both plan tiers** — the API rate limit depends on it (Growth ≈ 200/min, Pro ≈ 400/min, Enterprise ≈ 700/min; **trials are hard-capped at 50/min**). This sets the runtime estimate.
- [ ] **Agent email map** — for any agent whose email differs between source and target (`mapping.agents`). Everything else auto-matches.
- [ ] **Custom-status / type / source / product value list** — if the target uses custom picklist values, I need the source→target value pairs (the tool passes these through today; unmapped values fail whole tickets).
- [ ] **A source "View" or count anchor per batch** — Freshdesk Views have no API, so you translate a view's rules into `tickets.filters` and reconcile against the human-visible count. This is how we prove "N in = N out" batch by batch.
- [ ] **Confirmation the client accepts** the known API limitations: no back-dated timestamps, no SLA history, no parent-child links, no time logs (unless we build it).

## PART C — Target-account preparation (operational, before every run)

**This is mandatory and non-negotiable in as-is mode — otherwise real customers get emailed.**
Full detail in `MIGRATION_GUIDE.md`. Summary:
- [ ] Turn **OFF all notifications** (agent + requester email notifications).
- [ ] Turn **OFF all automations / business rules** (Dispatch'r, Supervisor, Observer, Scenario automations).
- [ ] Turn **OFF SLA reminders/escalations** and CSAT surveys.
- [ ] Turn **OFF "reopen on customer response"** (the tool re-applies status, but disabling avoids churn).
- [ ] Disable **parent-child ticketing** during the run (HDM says the same).
- [ ] Pre-create the `created_date_custom_field` (text) if using A2.
- [ ] Ensure enough disk for streamed attachments (temp dir); the tool cleans up per file.
- [ ] Re-enable everything **after** cutover.

---

## PART D — Engineering work to reach production grade (I do this; you prioritize)

Ordered by confidence-per-effort. Items map to the review (§6/§10/§11) and SUGGESTIONS.

**D0 — Fix the two known defects (small, do immediately) — ✅ DONE 2026-07-02**
- ✅ `include_attachments` flag now wired in `tickets.py` (ticket + conversation paths).
- ✅ Stale docs corrected (custom-field auto-create already exists; HDM gap note obsolete).

**D1 — Completeness reconciliation vs source — ✅ DONE 2026-07-02**
`verify --deep` re-reads the source id-space (`completeness_report` in `reconcile.py`)
and writes `completeness.csv` listing any source record with no successful target
(MISSING/partial/failed), plus a PASS/INCOMPLETE line per entity. Widen with
`--completeness ticket,contact,company`. (review R2)

**D2 — Archived-ticket handling (CRITICAL for large/old clients) — BUILT; detection verified, ingestion unverified vs real data (2026-07-02)**
Confirmed via API: Freshdesk's ticket LIST endpoint OMITS archived tickets entirely, there
is NO archived-list API, and archived tickets are reachable only via per-id
`GET /tickets/archived/{id}` (a plan without the feature returns `403 require_feature`).
Because the run AND the completeness check both enumerate via the list, archived tickets are
invisible to both — so completeness.csv does NOT reveal them (correction to the earlier note).
Built: (a) `check` now DETECTS the archive feature and loudly warns if it's ON but
`archived_scan` is off ("would be SILENTLY MISSED"); (b) opt-in `tickets.archived_scan`
(+ `archived_id_min`/`archived_id_max`) sweeps the id range, fetches each archived ticket, and
reuses the SAME payload/create/replay path as live tickets, deduped against idmap + markers;
(c) `completeness_report` now prints that its ticket scope is LIVE-only. Detection + the
feature-unavailable branch are verified live on the test pair; the actual archived read+create
path is UNVERIFIED (test plan has no archived data) — **must be validated on a real archive-
enabled client account (residual risk R-A in GO_LIVE_VALIDATION_REPORT.md).** For very large
old accounts an id-sweep may be too many calls — the Account Export API (`archive_tickets`
resource) is the documented bulk alternative, not yet built. (review R1)

**D3 — Deep field-level verifier (`verify --deep`) — ✅ DONE 2026-07-02**
`deep_verify` in `reconcile.py` diffs subject, body (banner-tolerant), status/priority/
source/type (value-mapped), requester email, responder, group, tags, custom-field
values, and conversation count on a ticket sample → `verify_deep.csv`. By-design gaps
(unmapped agents, strict-dropped fields) are not flagged. Unit-verified. This is the
certificate you hand a client. (review §11)

**D4 — Pre-flight value/field coverage in `check` — ✅ DONE 2026-07-02**
`preflight_coverage` in `reconcile.py` runs inside `check`: it reads both accounts'
`/ticket_fields`, resolves every source status/priority/source value through the
configured value maps and every `type` string, and WARNs on any value with no valid
target (those tickets would fail mid-run). It also summarizes which source custom
fields the target already has vs. the ones the custom_fields phase will create.
Live-verified against the test pair; negative-tested (correctly flags a missing
status/source/type). Catches ticket failures before the run instead of during it.
(review §10, R5)

**AUTO MODE — ✅ DONE 2026-07-02** (`runner.run_auto`, `python -m fdmigrate auto`)
One self-verifying command for the whole go-live sequence with hard gates: Gate 1
connectivity → Gate 2 value coverage (abort unless `--force`) → foundation → Gate 3 ticket
sample + deep-verify (abort before the full volume if the sample isn't field-clean) → full
run → Gate 4 completeness + deep-verify certification (→ optional `--delta`). Resumable;
`--sample N`, `--dry-run`. Live-verified (all gates PASS on the test pair; Gate 1 abort
proven) + 7 unit tests on the gate control flow. Turns OPERATOR_QUICKSTART §3–§6 into one
command; §1 target-prep and §7 cutover remain manual.

**D5 — Per-value picklist mapping** for Type/Source/Product (parity with HDM).

**D6 — Inline-image rehosting** so migrated ticket bodies don't show broken images.

**D7 — Operability — ✅ MOSTLY DONE 2026-07-02** (marker-prefetch cache still open)
Done + validated on the test pair: `--dry-run` (target writes intercepted+logged, zero writes -
confirmed target untouched; caveat: over-reports contact/company creates since it can't see the
409 "already exists" response); live **ETA** in the ticket heartbeat (when a limit/total is known);
consolidated **end-of-run summary** with failed/partial counts + next-step hints; **`retry`**
(clears failed/partial then re-runs only those); **`reset --entity`** (forget an entity's state);
and local-only commands (`status`/`reset`) no longer require API keys. STILL OPEN: caching the
marker prefetch to remove the O(n) pre-scan at 100k+ (part of D9). (SUGGESTIONS S-07–S-14)

**D8 — Rollback / undo — ✅ DONE (tickets) 2026-07-02** (`python -m fdmigrate rollback`)
Deletes only the tickets THIS TOOL created, identified by their `fd-migration-*` marker tag -
so client data (matched/updated contacts/companies/agents/groups) is NEVER touched, which is
exactly the safety the `origin`-column idea was for (marker tags already encode "we created
this"). Previews by default; `--yes` to delete; supports `--dry-run`. Live-verified preview
(correctly found the 107 tool-created tickets, excluded pre-existing ones) + 5 unit tests
(preview/dry-run delete nothing, --yes deletes + cleans local state, partial-failure -> rc 2).
NOTE: intentionally tickets-only - bulk-deleting migrated contacts/companies is dangerous
(they're often the client's real records), so those stay manual. Addresses the original
"rollback feasibility" concern from the readiness review.

**D9 — Concurrency** for 100k–500k jobs (bounded, rate-header-aware) + attachment URL
re-fetch on expiry. (review D-block)

**D10 — Automated test suite — ✅ DONE 2026-07-02**
`tests/` — 42 stdlib `unittest` tests (no deps, no network) over the pure logic:
`_build_payload` (value mapping/marker/due-date guard/requester cascade/CF strictness),
D4 `preflight_coverage` + field-choice parsing, D3 `_compare_ticket`, `_create_ticket`
retries (company_id + due_by), store (upsert/target_map/clear_failed/reset_entity/conv
dedup), D2 `archived_feature_state`, D7 dry-run suppression. Run:
`python -m unittest discover -s tests -v`. Guards the live-validated behavior against
regression (run after any package change).

Minimum set to certify a **mid-size** client (≤~50k, recent history): **D0, D1, D3, D4** — all ✅ DONE.
Additional set for a **Fortune-500-scale** client: **D2, D9, D8.**

---

## PART E — Per-client go / no-go gate

Run this sequence for every client; do not proceed to the next step until the current one passes.

1. [ ] `check` passes: both accounts reachable, **agent gap = 0**, value/field coverage clean (D4).
2. [ ] Target prepared per Part C (notifications/automations/SLA off). **Verified, not assumed.**
3. [ ] **Sample gate:** `run --only tickets --limit 50`, then `verify --deep` (D3) → 100% field match on the sample.
4. [ ] Completeness baseline recorded (D1): source totals captured.
5. [ ] **Batch runs** by filter (e.g. per status/date window), reconciling each batch against its source View count.
6. [ ] Full reconciliation: `reconciliation.csv` shows every source record → target id or a documented exception; **completeness delta = 0** (D1).
7. [ ] Attachment manifest reviewed: every non-migrated attachment has an acceptable reason.
8. [ ] **Delta pass** (`run --delta`) at cutover to catch anything changed since the main run.
9. [ ] Re-enable target notifications/automations. Client sign-off on the verifier report.

---

### Bottom line for you
- **You provide:** credentials + plan tiers, the agent/value maps, per-client count anchors, and the target-prep (Part C). You decide A1–A5.
- **I build:** D0–D4 to certify mid-size clients; D2/D9/D8 to reach Fortune-500 scale.
- **The gate (Part E)** is what makes each individual migration trustworthy regardless of size.
</content>
