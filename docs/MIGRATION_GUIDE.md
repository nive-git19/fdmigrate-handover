# Freshdesk → Freshdesk Migration Runbook (real client jobs)
How to actually run a migration with this tool, safely and batch-wise. Read fully before starting.

---

## ⚠️ WHY pre-flight matters: what fires during a migration if you DON'T disable it
Creating/updating records via the API triggers the **target account's** normal notifications &
automations. During a bulk migration that means hundreds–thousands of emails + rule executions.
(In our test, a single account's inbox hit **173** unread from this alone.)

### What gets triggered, by role
| Role | What they get blasted with (if not disabled) |
|---|---|
| **End users / customers** ⚠️ most dangerous | "Ticket received" acknowledgements, "ticket resolved/closed" emails, **CSAT/satisfaction survey** emails on resolve, reopen confirmations. On a production target this emails *real customers* about years-old tickets. |
| **Agents** | "Ticket assigned to you", "ticket reopened", "note added/mention", "new ticket created", SLA breach/escalation alerts. Floods agent inboxes. |
| **Admins** | Automation-rule side effects, **deliverability/bounce alerts** (high bounce → Freshdesk auto-**blocks outgoing email**, as we hit), account-activity digests. |

> Our tool already minimizes customer risk: conversations migrate as **notes** (notes never email),
> public/private preserved; we never use emailing `/reply` unless you opt in. But **automations,
> SLA, and the account's notification settings are outside the tool** — you MUST disable them.

---

## ✅ PRE-FLIGHT CHECKLIST (do every item before migrating)

### On the TARGET account (Admin →)
- [ ] **Email Notifications** → turn OFF all **Agent** notifications and all **Requester** notifications
- [ ] **Automations** → disable **Dispatch'r** (ticket creation rules), **Observer** (update rules), **Supervisor** (time-triggered), and **Scenario Automations**
- [ ] **SLA Policies** → disable SLA reminder/escalation emails (or accept timers recalc from migration date)
- [ ] **"Reopen on response"** behavior → disable (so migrated notes don't reopen closed tickets; our tool also re-applies status, but belt-and-suspenders)
- [ ] **CSAT / Satisfaction surveys** → disable (don't survey customers on migrated resolves)
- [ ] **Outbound email** → confirm not already blocked; keep volume low
- [ ] **Pre-create** anything the API can't: **custom statuses**, **business hours**, **agent accounts** (or rely on our match-only); custom **fields** are auto-created by our tool
- [ ] Confirm **plan tier** (rate limit) and consider asking Freshworks for a **temporary limit increase**

### On the SOURCE account
- [ ] If you SEED test data into source, **disable source outbound notifications too** (fake requester emails bounce → triggers the outgoing-email block)
- [ ] Disable **parent-child ticketing** during migration (HDM recommends this too)
- [ ] Source stays **read-only** during/after migration (we never write to it)

### Mapping inputs (give to the tool / config.yaml)
- [ ] `mapping.agents`: source-email → target-email for every agent whose email differs
- [ ] `mapping.status` / `priority` / `source`: only if target ids differ
- [ ] Confirm group names; the tool auto-creates missing groups
- [ ] Decide conversation visibility: `force_public_notes` (default = preserve source)

---

## 🚦 BATCH-WISE MIGRATION — the actual run order

**Step 0 — Connection check (read-only)**
```
python -m fdmigrate check --config config.yaml
```
Confirms both accounts + shows source counts.

**Step 1 — Schema + foundation (fast, low risk)**
```
python -m fdmigrate run --only custom_fields,agents,companies,contacts,groups --config config.yaml
```
Creates custom fields on target, matches agents, migrates companies/contacts/groups. Verify counts.

**Step 2 — SAMPLE ticket batch (the sign-off gate) 🚦**
```
python -m fdmigrate run --only tickets --limit 25 --config config.yaml
python -m fdmigrate verify --config config.yaml
```
Open a few migrated tickets: custom fields populated? conversations as notes? attachments? status correct?
**Do not proceed until this batch is clean.**

**Step 3 — Tickets in verifiable batches** (slice by date, oldest first — edit `tickets.filters` in config):
```yaml
tickets:
  filters: { created_after: "2020-01-01", created_before: "2021-01-01" }
```
```
python -m fdmigrate run --only tickets --config config.yaml   # batch 1
# verify → change the window → batch 2 → verify → ...
```
Each batch is resumable + idempotent (re-run skips done, marker-tagged). Watch the heartbeat; check
`status` from a 2nd terminal anytime.

**Step 4 — Business + KB objects**
```
python -m fdmigrate run --only canned_responses,knowledge_base --config config.yaml
```

**Step 5 — Reconcile**
```
python -m fdmigrate verify --config config.yaml
```
Review `reports/failures.csv`, `reports/attachment_manifest.csv`, `reports/reconciliation.csv`.
Re-run to retry failures (idempotent).

**Step 6 — Delta pass (catch tickets created DURING the migration)**
Set `tickets.updated_since` to the migration start time and re-run `--only tickets`. Final short
**source freeze** → last delta → done.

**Step 7 — Cutover (only after sign-off)**
- Re-enable the target's **notifications + automations + SLA** that you disabled
- Redirect support channels (email/portal) to the target
- Keep source **read-only** as a safety net for a grace period

---

## Tool behaviors to know (so results aren't a surprise)
- **Created date** can't be back-dated via API → preserved as a **provenance banner** in the body.
- **Conversations** = notes (never email); visibility preserves source; `force_public_notes` to force public.
- **Closed tickets** stay closed — status is re-applied after notes.
- **Custom fields** auto-created on target (ticket/contact/company); values remapped by name/label; nulls dropped.
- **Dedup** via `fd-migration-<id>` tag + SQLite idmap → safe re-runs, no duplicates.
- **Due dates** only migrate if still in the future (past dates invalid on a now-created ticket).
- **Out of scope** (neither tool does via API): business rules/automations, SLA policies, custom objects, reports.
