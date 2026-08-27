# Go-Live Validation Report — Freshdesk → Freshdesk Migrator

_Prepared as Migration Architect. Records the pre-client validation executed
against a live test pair, what is certified, and the residual risks that MUST be
closed with a real client account before a Fortune-500-scale production run._

- **Date:** 2026-07-02
- **Tool:** `fdmigrate` (canonical copy)
- **Source (read-only):** `sdkinfinity1684.freshdesk.com` — 2 agents, 1 group, 7 companies, 13 contacts, 107 seeded "PILOT" tickets
- **Target (write):** `oskloud-supportdesk.freshdesk.com`
- **Mode:** `conversations_as_notes: true` (zero-customer-email), target outbound email confirmed OFF by account owner

---

## 1. Validation results

| # | Test | Method | Result |
|---|------|--------|--------|
| V1 | Connectivity + auth (both accounts) | `check` | **PASS** |
| V2 | Pre-flight value coverage (D4) | `check` — status/priority/source/type resolved on target | **PASS** (7/4/17/5 all resolve); negative-tested, catches gaps |
| V3 | Agent-gap detection | `check` | **PASS** — flags `Dinesh <dinesh@sdkinfinity.com>` (no target agent) |
| V4 | Custom-field presence | `check` | **PASS** — all ticket/contact/company fields present |
| V5 | Full-object migration | full pipeline run | **PASS** — 7 companies, 13 contacts, 1 group, canned, KB, **107/107 tickets, 0 failed** |
| V6 | Completeness "N in = N out" (D1) | `verify --deep` | **PASS** — ticket 107/107, contact 13/13, company 7/7, 0 missing |
| V7 | Field-level fidelity (D3) | `verify --deep` | **PASS** — 25/25 sampled tickets clean, 0 mismatch |
| V8 | Ticket + conversation attachments | direct source-vs-target compare (#107→#163) | **PASS** — all 3 ticket files + 1 conversation file carried over |
| V9 | Conversation threading & privacy | direct compare | **PASS** — 6/6 conversations, order + bodies intact, **private note stays private** |
| V10 | Due-date / SLA-timer edge case | pilot surfaced it | **FIXED** — drop-and-retry when target status has no SLA timer (was 1 fail → 0) |
| V11 | Create-path refactor safety | delete 2 + re-migrate | **PASS** — recreated exactly 2, 0 failed, via shared `_create_ticket` |
| V12 | Idempotency (no duplicates on re-run) | re-run full tickets | **PASS** — 105 skipped, 0 created, 0 dups |
| V13 | Crash-resume across process restart | migrate 4 → "crash" → resume | **PASS** — resumed, created remaining 6, **0 duplicates** |
| V14 | Post-churn integrity | count unique markers on target | **PASS** — 107 unique markers, **0 duplicate markers** after 12 delete/recreate cycles |
| V15 | Archived-ticket detection (D2) | `check` + live code exercise | **PASS (detection)** — correctly reports feature not on plan; ingestion path executes cleanly |

**Headline:** every source ticket is accounted for, field-accurate, de-duplicated,
attachment-complete, and crash-safe on the test pair.

---

## 2. Certified capabilities (proven on live data)

- No data loss: completeness reconciliation re-reads the source id-space and proves 0 missing.
- No duplicates: two-layer dedup (local SQLite checkpoint + target marker tags) survives
  process restarts and repeated runs — verified with 0 duplicate markers after deliberate churn.
- No lost attachments: ticket- and conversation-level files migrate; a manifest records any exception.
- Correct placement/fidelity: subject, status, priority, type, tags, requester, responder,
  group, private/public visibility, and conversation order all preserved (field-level verifier).
- Crash safety: kill at any point, re-run resumes from the checkpoint; no re-creation.
- Pre-flight safety: `check` catches unmapped agents and any status/priority/source/type value
  with no valid target BEFORE the run, so tickets don't fail mid-migration.

---

## 3. Known limitations & by-design trade-offs (disclose to every client)

1. **Notes-mode flattens conversation direction.** In `conversations_as_notes: true`
   (the zero-email default), every message becomes a note — content, privacy, and
   portal-visibility are preserved, but the `incoming` flag and the reply-vs-note
   type are not, so a customer's message won't render as an "incoming" reply.
   This is the cost of guaranteeing zero customer emails and matches HDM's approach.
   _If a client requires true incoming rendering: use as-is mode with target
   notifications fully disabled (belt-and-suspenders now in place)._
2. **Timestamps cannot be back-dated** (Freshdesk API limitation). Migrated tickets/
   notes carry the migration date; originals survive via the provenance banner and/or
   `created_date_custom_field`. Same limitation HDM works around only via a partner API.
3. **Custom-field VALUE migration is code-covered but not data-tested here** — the test
   dataset has fields defined but all values empty. Prior sessions tested `cf_reference_number`;
   re-confirm on any client whose tickets carry custom-field values.
4. **Archived tickets: detection verified, ingestion UNVERIFIED against real data.**
   The test plan lacks the Archive Tickets feature, so `check` correctly reports "none
   exist," and the ingestion code runs the feature-unavailable path cleanly — but the
   actual archived read+create path has NOT been exercised against real archived tickets.
5. **Zero-email guarantee is mechanism-verified, not inbox-verified.** Notes never email and
   target outbound email was off, but I cannot read the customers' inboxes to prove silence.

---

## 4. Residual risks — MUST close with a real client account before Fortune-500 go-live

These cannot be certified on a trial and are the true gate to a large production run:

- **R-A — Archived ingestion at real scale.** Point `archived_scan` at a client account
  that has the Archive feature + archived tickets; confirm the read/create/conversation
  paths and reconcile counts. (Limitation #4.)
- **R-B — Throughput at volume.** Trials are hard-capped at 50 calls/min; a 150k-ticket job
  needs the client's real plan tier (Pro ≈ 400/min, Enterprise ≈ 700/min) and likely a
  temporary Freshworks rate-limit increase. Rehearse on real volume (a ~15k-ticket
  engagement is the interim proxy) to get a real ETA and confirm the single-threaded design is acceptable
  (else build D9 concurrency).
- **R-C — Custom-field values** on real client tickets (Limitation #3).
- **R-D — Target-prep discipline.** The zero-email guarantee depends on the target's email/
  automations/activation being OFF (Runbook Part C). This is an operational step, verified
  per client, not something the tool can enforce.

---

## 5. Go / No-Go recommendation

- **Mid-size client (≤~50k tickets, recent history): GO**, subject to the per-client Part E
  gate (check clean → target prepped → sample gate → batch runs → completeness delta 0 →
  delta pass → sign-off). The tool is validated end-to-end for this profile.
- **Fortune-500 scale (100k–500k, years of archived history): CONDITIONAL** — GO only after
  R-A and R-B are closed on the actual client account, and after deciding on D9 (concurrency)
  and D8 (rollback) based on the client's ETA tolerance and contractual needs.

---

_Artifacts from this validation live in `reports/`: `reconciliation.csv`,
`completeness.csv`, `verify_deep.csv`, `ticket_spot_check.csv`, `failures.csv`,
`attachment_manifest.csv`._
