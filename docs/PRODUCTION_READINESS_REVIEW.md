# Production Readiness Review — Freshdesk Migration Tool
_Assessment date: 2026-07-02. Reviewer role: Migration Architect / Principal Engineer / QA Lead / PM._
_Scope: full read of `fdmigrate/` (~2,434 LOC), HANDOFF.md, HDM_REFERENCE.md, SUGGESTIONS.md, live config.yaml. No code changed — assessment only, per request._

> Verdict up front: this is a **genuinely well-engineered tool**, not a prototype. The reliability core (SQLite checkpointing, date-windowing past the 30k cap, tag-based ticket idempotency, reconciliation CSVs, resume-on-crash) is real and mostly correct. It is **trustworthy today for controlled, mid-size migrations (≤ ~20k tickets, recent history, one operator watching it)** — e.g. the ~15k and ~52k-ticket engagements in the pipeline, after the fixes below. It is **not yet trustworthy for a true Fortune-500 instance** (hundreds of thousands of tickets + years of *archived* history) because of five specific gaps documented in §6. **Production Readiness Score: 65/100** (see §14).

---

## 1. Architecture Review

**Structure (good).** Clean layering: `client.py` (HTTP/rate-limit/pagination) → `store.py` (SQLite state) → `phases/*` (one module per entity, uniform `run(ctx)->dict`) → `runner.py` (orchestration) → `reconcile.py` (reporting) → `__main__.py` (CLI). `base.py` supplies a shared `Context` and `Heartbeat`. The dependency-ordered `PHASE_ORDER` (custom_fields → agents → companies → contacts → groups → tickets → canned → kb) is correct.

**Strengths**
- Single write surface: only the target client is ever written to; source is read-only by construction. Good blast-radius control.
- Every state mutation is committed immediately (`store.py` commits per call) → `kill -9` loses at most the record in flight.
- Idempotency is designed in, not bolted on: tickets carry a `fd-migration-{source_id}` marker tag and are checkpointed `partial`→`done`.
- Errors are contained per-record (`except Exception` around each ticket) so one bad record never kills a multi-day run.

**Weaknesses / recommended refactors (not blockers)**
- **Two divergent custom-field code paths.** Tickets use `_map_custom_fields` (strict-drop) while contacts/companies use `remap_cf`. Same intent, different logic and different failure modes. Consolidate to one.
- **Marker prefetch is O(all target tickets) on every run** (`_prefetch_target_markers`). At 156k tickets that's ~25 min of scan *before the first source ticket*, repeated on every resume (SUGGESTIONS S-08, still open). The local idmap already dedups; the prefetch is a secondary safety net that should be cached in SQLite and/or skipped when the DB is present.
- **No abstraction for "created vs matched".** `store.upsert(...status='done')` is used identically whether a contact/company was *created* by us or *matched* to a pre-existing target record. This has no effect on migration but **breaks safe rollback** (see §Rollback).
- **`Config` is a flat god-object.** Fine at this size; if it grows, split per-phase config dataclasses.

---

## 2. Feature Gap Analysis (against the stated feature list)

| Capability | State in code | Notes |
|---|---|---|
| Contacts / Companies / Tickets | ✅ | Windowed past 30k for contacts+tickets; companies capped (API can't window) |
| Conversations / public replies / private notes | ✅ (as-is mode) | **Currently disabled by live config** — see §3 |
| Attachments (ticket + conversation) | ✅ | Streamed to disk, size-capped, manifested. **Inline images NOT rehosted** (§6) |
| KB articles | ⚠️ code exists, **untested**, **no article attachments/images** (S-13) |
| Canned responses | ✅ | Flattened (folders dropped by design) |
| Custom fields (ticket/contact/company) | ✅ **incl. auto-create on target** | Contradicts the stale "we require pre-creating" note — see §7. Caveat: nested/dependent fields unhandled |
| Agent / Group mapping | ✅ | Agents match-by-email only, no create, no default fallback (by design) |
| Status / Priority / Source mapping | ✅ (config) | Identity by default |
| **Type / Product(Brand) mapping** | ❌ | Type passed through unmapped (fails if target lacks the value); Product dropped+logged |
| Department mapping | N/A | Freshdesk has no departments (that's Freshservice) |
| Incremental / delta migration | ✅ | `run --delta`, cursor-based, live-verified |
| Progress tracking | ✅ | Heartbeat with rate/elapsed (no ETA yet, S-07) |
| Detailed logs | ✅ | File + console + SQLite `events` table |
| Retry failed records | ⚠️ partial | Re-run retries `partial`/failed tickets; **no `retry --failed` command** targeting `failures.csv` |
| Validation reports / reconciliation | ✅ | Full idmap accounting + failures + attachment manifest + 25-ticket spot-check |
| Pre-migration analysis | ⚠️ | `check` does connectivity + counts + agent-gap. **No mapping/field/value pre-flight** (§10) |
| Post-migration verification | ⚠️ shallow | Spot-check = subject + conversation *count* only. No field-level diff (§11) |

---

## 3. The "customer replies became notes" investigation (validated, not assumed)

**Root cause: configuration, not a code defect.**

- The conversation replay logic in `tickets.py:_replay_conversations` is **correct** in its default (`conversations_as_notes: false`) mode:
  - `incoming` (customer) message → `POST /tickets/{id}/notes` with `incoming:true, private:false` authored by the mapped contact → renders in-thread as the customer's inbound message. **This is exactly the technique HDM uses** (the Freshdesk public API has no way to inject a genuine inbound email; the `incoming:true` note is the correct and only mechanism).
  - agent public reply → `POST /tickets/{id}/reply` (a real reply).
  - private/public note → `POST .../notes` preserving `private`.
- **Your live `config.yaml` sets `conversations_as_notes: true`** (the legacy "everything becomes a note; notes never email" safe mode). In that mode *every* conversation — including customer replies and agent replies — is posted as a note. **That is the behavior you observed.**

**Recommendation.**
1. For production fidelity, run with `conversations_as_notes: false` **and** disable all target notifications/automations first (mandatory — otherwise real `/reply` calls email live customers).
2. Keep `conversations_as_notes: true` only for dry tests where you cannot guarantee notifications are off.
3. Metadata *is* partially lost either way — see the timestamp finding in §6 (all replayed conversations get "now" as their timestamp; original per-message time is only preserved if `provenance_banner: true`, which is off by default).

**Residual real issue (not config):** even in as-is mode, non-reply/non-note channel messages (forward, phone, social, source codes other than 0/2) are collapsed into notes. Acceptable (the target can't re-emit those channels) but should be *logged per message* so the operator knows.

---

## 4. Comparison with Help Desk Migration (using HDM_REFERENCE.md as oracle)

**Where we already match or exceed HDM**
- Resumable + idempotent + dedup (marker tags + SQLite) — HDM is opaque here; we are auditable.
- Reconciliation CSVs + attachment manifest (we expose every non-migrated attachment with a reason).
- Match-wise filtering (status/group/tag/date/requester/company) — richer than HDM's basic filters.
- Delta/incremental — parity.
- **Auto-create custom fields** — we do it automatically for all three entities; HDM makes it a manual one-click. This is the feature you asked for and it **already exists** (§7).
- You own the code; zero per-ticket cost.

**Where HDM still wins (ranked by importance for you)**
1. **Timestamp fidelity** — HDM (as a Freshdesk partner) can back-date created/updated/closed dates via a private bulk API. We cannot (public API rejects back-dating). This is the single biggest fidelity gap and is **not fully closeable** without partner access. Mitigations: `created_date_custom_field` (created date only) + provenance banner (currently off).
2. **Per-value mapping for Type / Source / Product(Brand)** — HDM maps every picklist value; we only map status/priority/source and *pass Type through unmapped* → ticket failures when the target lacks a source type/brand value.
3. **Rollback** — HDM offers rollback of a demo/migration; we have none (see §Rollback — it's buildable).
4. **Inline images migrated as attachments** — HDM default-on; we don't rehost inline images (§6).
5. **Time logs** — HDM migrates them; we don't.
6. **Create-agents-on-target + default-agent fallback** — we deliberately match-only. Reconsider only if a client needs it.
7. **A wizard UI with a Demo step** — we're CLI-only (§5).

**Same scope boundary as HDM (not gaps):** Groups, SLA policies, automations/business rules, parent-child links are not migratable objects in either tool.

---

## 5. UX Review (CLI is the interface today)

The CLI is coherent for an engineer but **not yet "hand it to an implementation consultant" ready.**
- ✅ Good: `check` → `run --only ... --limit` → `run` → `status`/`verify` flow; resumable; background-run guidance.
- ❌ Gaps:
  - **No `--dry-run`** (S-09). A consultant needs a no-write preview: counts, unmapped values, missing fields/agents, estimated duration.
  - **No ETA** in the heartbeat (S-07) — on a 2–4 day run this matters.
  - **No end-of-run summary** with total wall-clock and per-entity totals (S-10).
  - **No failed-record viewer / `retry --failed`** — you get `failures.csv` but must hand-re-run.
  - **Mapping is YAML-only.** For Type/Source/Status this means the consultant must know source *value ids*. HDM's per-value dropdowns are much safer. Minimum bar: a `check` that *enumerates the distinct source values* for each mapped field so the operator can see what needs mapping.
  - No visible confirmation screen before a destructive full run.

A thin **read-only web dashboard** (progress, reconciliation, failures) over the SQLite DB would close most of the "feels reliable" gap without rebuilding the engine. This is where the earlier Odoo-UI effort should be redirected — a *reporting* UI, not an execution UI.

---

## 6. Reliability Risks (correctness-first, ranked)

**R1 — Archived tickets may be silently missed. [CRITICAL for large/old instances]**
`iter_tickets` uses `/tickets?updated_since=...&order_by=updated_at`. Freshdesk **archives** old resolved/closed tickets; archived tickets are generally **excluded from the standard list endpoint**. For a Fortune-500 with years of history, archived tickets can be the *majority* of the dataset. The tool has no archived-ticket handling and **no completeness check against a source total**, so this loss would be **invisible**. → Must be verified per-account before any large run; likely needs the archived-ticket API or search API. This is the #1 reason not to trust it at scale yet.

**R2 — No completeness reconciliation vs the source. [CRITICAL for "confidence"]**
`reconcile.py` reports one row per record *the tool attempted*. If a source ticket was never enumerated (archived, or `iter_windowed` hit the pathological same-timestamp stop at `client.py:189`, or a filter excluded it), it simply never appears — reconciliation shows "all done" while data is missing. → Add: fetch source total count, compare to `done` count, report the delta explicitly.

**R3 — Timestamp fidelity loss. [HIGH]**
All replayed conversations are created "now"; ticket created_at cannot be back-dated. With `provenance_banner: false` (default) the original per-message timestamps are **not preserved anywhere on the target.** Thread *order* is kept, absolute times are lost. → At minimum default the note provenance line ON, or stamp original time into the note body.

**R4 — In-flight crash can duplicate a ticket IF marker-prefetch is disabled. [MEDIUM]**
Create-ticket POST and the `store.upsert(...partial)` are two steps. If the process dies between them, the target has a marker-tagged ticket with no idmap row. On rerun, dedup relies on `check_target_markers` (default true) catching the tag. If an operator sets `check_target_markers: false` for speed, that crash window produces a **duplicate ticket**. → Document loudly, or write a pre-create "intent" row.

**R5 — Type/Source/custom-status value gaps fail whole tickets. [MEDIUM]**
Unmapped `type` (and non-default statuses/sources) are passed through; if the target lacks the value the create 400s and the entire ticket fails. No per-value fallback. → Pre-flight enumeration + per-value mapping.

**R6 — `include_attachments: false` does nothing. [LOW, but a correctness/trust bug]**
Flag parsed in `config.py:74`, referenced nowhere. Attachments always migrate. → Wire it or remove it.

**R7 — `iter_windowed` pathological stop. [LOW]**
>30k records sharing one `updated_at` → stops and warns, truncating. Rare (bulk imports). → Fall back to id-based paging in that case.

**R8 — Groups membership PUT overwrites `agent_ids`. [LOW]** On a pre-populated target group this can remove existing members. Fine on a clean target.

---

## 7. Custom-field auto-creation (you asked for it — it's already here)

`custom_fields.py` runs first and **creates missing ticket/contact/company custom fields on the target automatically** (matched by name, then label), then builds a source→target name map so values remap correctly. This *exceeds* HDM's manual one-click. The HDM_REFERENCE gap note ("we require pre-creating them") is **stale** — verify and delete it.

**Caveats to harden before trusting it on complex schemas:**
- **Nested/dependent fields** (`nested_field`, Country→State→City) — `_normalize_choices` flattens choices; the create payload for nested/dependent fields needs a hierarchical structure and will likely fail. Handle or explicitly skip+warn.
- **Sections / dynamic field visibility** — not migrated.
- Ticket field **position capped at 15**; extra fields pile at the bottom.
- Failures are logged but a failed field-create then causes silent value-drop downstream (strict mode). Surface field-create failures in the pre-flight, not mid-run.

---

## 8. Rollback — honest technical assessment

**Verdict: yes, buildable, and the data model is 80% there — but not safely, today.**

The SQLite `idmap` records every `(entity, source_id → target_id)` we touched, so a rollback can iterate it and `DELETE` target records in reverse dependency order (tickets → contacts → companies → groups → custom fields). Tickets are always created by us, so deleting them is safe.

**The blocking design flaw:** the store does **not distinguish "created" from "matched/updated".** A contact or company that already existed on the target and was merely *matched* (or *updated*) is stored identically to one we created. A naive rollback would **delete pre-existing customer records we never created** — catastrophic.

**Design to do it properly:**
1. Add an `origin` column to `idmap`: `created | matched | updated`. Only `created` rows are eligible for deletion.
2. `rollback` command: reverse-dependency order, delete only `origin='created'`, entity-scoped (`--only tickets`) and dry-run-first (`--dry-run` prints what it would delete).
3. Custom fields: deleting a field deletes its data — make field rollback opt-in and last.
4. Deletions are soft in Freshdesk (tickets go to trash, contacts can be hard-deleted) — document the semantics per entity.
5. Rollback must tolerate partial failures and be itself resumable (reuse the checkpoint pattern).

This is a focused, high-value build once the `origin` column exists.

---

## 9. Security Review

**Good**
- No literal API keys in `config.yaml` (env refs); `config.yaml`, `*.db`, `reports/`, `logs/`, `_attachments_tmp/` all git-ignored.
- Env-var / `env:` indirection for secrets (`config.py:_resolve_key`).
- Refuses source==target (`config.validate`).
- Attachments streamed and deleted per-file after upload.

**To address**
- **PII at rest, unencrypted:** `migration.db` stores subjects and error text; `events` and `failures.csv` store `resp.text[:250]` which can include email/body fragments. Attachment temp files contain full customer files. → Document retention/secure-delete; consider redacting bodies from logged error text.
- **Orphaned temp files on crash:** `_cleanup` runs per successful upload; a crash mid-ticket leaves customer attachments in `_attachments_tmp/`. → Add a startup sweep and a documented purge.
- **No audit of *who* ran the migration / when** beyond log timestamps. For enterprise engagements add an operator/run-id header to the run.
- Retry `allowed_methods` includes POST/PUT/DELETE with `raise_on_status=False` — combined with the manual 429/5xx loop this is fine, but a retried non-idempotent POST at the transport layer is a (small) duplicate risk for non-ticket entities that lack tag dedup. Worth confirming the transport retry only fires on connection errors, not on completed 5xx (the code's manual loop already handles 5xx).

---

## 10. Migration Validation Framework (proposed pre-flight)

`check` today does: connectivity (both sides), source counts, agent-gap. Extend to a full **pre-flight gate** that must pass before a full run:

- ✓ Source & target reachable + authenticated (have)
- ✓ Source ≠ target (have)
- ✓ Agent coverage: every source agent resolves on target (have) — add default-agent decision
- ✓ **Field coverage:** every source custom field exists or can be created on target; list nested/dependent fields that can't
- ✓ **Value coverage:** enumerate distinct source `status`, `priority`, `source`, `type`, `product` values and confirm each maps to a valid target value (catches R5 before it fails tickets)
- ✓ **Completeness baseline:** record source ticket/contact/company totals for later reconciliation (addresses R2)
- ✓ **Archived-ticket probe:** detect whether the source has archived tickets and warn they may be excluded (addresses R1)
- ✓ Target notifications/automations OFF confirmation (checklist item; API can't fully verify)
- ✓ Attachment temp dir writable + free-space estimate (sum of source attachment sizes)
- ✓ API-limit / duration estimate: (records × calls-per-record) ÷ plan rate → ETA + call budget
- ✓ Duplicate probe: count existing marker tags on target
- Output: a single PASS/WARN/FAIL report the consultant signs off on.

---

## 11. Testing Strategy

**Current state:** solid ad-hoc harness (`_gen_1k.py`, `_run_1k_test.py`, `_cleanup_*`, `_verify_*`) and one live 7-ticket end-to-end verification. **No automated test suite.**

**Recommended, before any client run:**
1. **Golden-ticket fixtures** covering every shape: plain, with attachments, inline images, 20+ conversation thread, private+public+incoming mix, custom-status, custom fields incl. nested, agent-raised requester, CC list, resolved+closed (status restore), oversize attachment, unavailable attachment (Freshcaller recording).
2. **Field-level verifier** (new `verify --deep`): for a sample, diff subject, description text, status, priority, requester email, responder, group, tag set, custom-field values, per-conversation body+visibility+incoming flag, and attachment count+names. Current spot-check (subject + conv count) is too shallow to certify a client.
3. **Replicate the mid-size client counts** (~15,000 / ~2,200 / ~190) end-to-end on trial accounts, then run the deep verifier and the completeness reconciliation — this is your existing NEXT step and it's the right one.
4. **Idempotency/resume tests:** kill mid-ticket, mid-conversation, mid-phase; assert zero duplicates and correct resume. (Partially proven; make it a repeatable script.)
5. **Unit tests** on pure logic: `_matches_filters`, `_map_custom_fields`, `_build_payload`, `_normalize_choices`, `iter_windowed` boundary/dedup, `strip_empty`.

---

## 12. Recommended Roadmap

**Phase A — Certify current scope for mid-size clients (before the first client run)**
- A1 Fix live config: `conversations_as_notes: false` + notifications-off checklist (or make an explicit decision to stay notes-only).
- A2 Completeness reconciliation vs source totals (R2).
- A3 Deep field-level verifier (§11.2).
- A4 Pre-flight value/field coverage enumeration (§10, R5).
- A5 Wire or remove `include_attachments` (R6).
- A6 Archived-ticket probe + explicit warning (R1 detection).

**Phase B — Fidelity & scale**
- B1 Inline-image rehosting as attachments (§6/HDM).
- B2 Per-value mapping for Type/Source/Product; pre-flight-driven.
- B3 Timestamp preservation policy (default provenance line on, or created_date field) (R3).
- B4 Marker-prefetch cache + `--refresh-markers` (S-08) — removes the 25-min pre-scan at scale.
- B5 ETA (S-07), end-of-run summary (S-10), `--dry-run` (S-09), `retry --failed`, `reset --entity` (S-14).

**Phase C — Enterprise features**
- C1 Rollback with `origin` column (§8).
- C2 Read-only web dashboard over SQLite (progress/reconciliation/failures).
- C3 KB article attachments/images (S-13) + KB verification.
- C4 Time-logs migration (if a client needs it).

**Phase D — Performance for 100k–500k**
- D1 Bounded concurrency for ticket detail-fetch + conversation posts (respecting rate headers).
- D2 Attachment URL-expiry re-fetch (S-12).

---

## 13. Priority Matrix

| | **High impact** | **Lower impact** |
|---|---|---|
| **Low effort** | A1 config fix · A5 attachments flag · B5 dry-run/ETA/summary · fix stale docs (§7) | S-15 Windows docs · A6 archived probe (detection only) |
| **High effort** | A2 completeness recon · A3 deep verifier · R1 archived-ticket ingestion · C1 rollback | B1 inline images · C2 web dashboard · D1 concurrency · C3 KB attachments · C4 time logs |

Do the top-left first: A1, A5, A2, A3, A4 give you the biggest confidence gain for the least code.

---

## 14. Production Readiness Score: **65 / 100**

| Dimension | Score | Rationale |
|---|---|---|
| Core correctness (create/dedup/resume) | 8/10 | Idempotency & checkpointing are real and mostly right |
| Completeness assurance | 4/10 | No source-total reconciliation; archived tickets unhandled |
| Fidelity | 6/10 | Bodies/attachments/requester good; timestamps & per-value mapping weak |
| Verification | 4/10 | Spot-check too shallow to certify a client |
| Scale (100k–500k) | 5/10 | Single-threaded + O(n) marker prescan; archived-ticket unknown |
| Operability / UX | 6/10 | Strong CLI, but no dry-run/ETA/summary/retry-viewer |
| Security | 7/10 | Good secret hygiene; PII-at-rest & temp-file cleanup to address |
| Rollback | 2/10 | None yet; data model 80% ready but unsafe without `origin` |

- **For mid-size clients (≤ ~50k, recent history):** ~**75%** after Phase A — usable with a watchful operator.
- **For a Fortune-500 (100k–500k + archived history):** ~**50%** until R1 (archived), R2 (completeness), D1 (concurrency), and C1 (rollback) land.

---

## 15. Actionable recommendations before touching the codebase

1. **Decide the conversation mode.** Confirm whether production runs go as-is (`conversations_as_notes: false` + notifications OFF) or stay notes-only. This one decision defines fidelity and must be settled first. *(config change, no code)*
2. **Confirm the archived-ticket question with a real large source** (does `/tickets?updated_since` return your oldest closed tickets?). This single fact decides whether the tool is safe at Fortune-500 scale. *(investigation, no code)*
3. **Agree the completeness contract:** every source record maps to a target id or a documented exception, proven against a source total. Build A2 next.
4. **Agree the verification bar:** field-level diff on a sample (A3) is the certificate you hand a client.
5. **Correct the stale docs** (custom-field auto-create already exists; HDM gap #1 is obsolete) so future sessions don't rebuild it.
6. Only then implement Phase A. No engine rewrite is warranted — the architecture is sound.
</content>
</invoke>
