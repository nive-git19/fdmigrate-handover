# Operator Quickstart — Running a Real Client Migration

_One page. Follow top to bottom. Do not skip a gate. Full detail: PRODUCTION_RUNBOOK.md
(decisions/inputs) and MIGRATION_GUIDE.md (target prep). Certification basis:
GO_LIVE_VALIDATION_REPORT.md._

## Fastest path (after step 1 target-prep): one command
Once the target is prepared (§1) and config is set (§2), `auto` runs §3–§6 for you with
built-in gates that stop on failure:
```bash
python -m fdmigrate auto --config config.yaml --dry-run   # rehearse, zero writes
python -m fdmigrate auto --config config.yaml             # real, gated, self-verifying
```
It aborts if connectivity, value coverage, or the ticket-sample fidelity check fails, and
certifies completeness at the end. Prefer the manual gates below when you want to inspect
each step (large/first-time clients). Either way, **§1 target-prep and §7 cutover are on you.**

## 0. Before you touch anything
- [ ] Get the client's **source** and **target** API keys (admin/Account-Administrator, Global Access).
- [ ] Confirm both **plan tiers** (sets the rate limit → the ETA): Growth ≈ 200/min, Pro ≈ 400/min, Enterprise ≈ 700/min. Trials are hard-capped at 50/min.
- [ ] Get the **agent email map** for any agent whose email differs source→target.
- [ ] Decide **conversation mode** (config `conversations_as_notes`): `true` = zero customer emails, but conversation direction is flattened (all become notes). `false` = true replies/incoming rendering, but REQUIRES every target email path off.

## 1. Prepare the TARGET account (MANDATORY — this is what protects customers)
In the target Freshdesk admin, turn **OFF** for the duration of the run:
- [ ] All **email notifications** (agent + requester).
- [ ] All **automations / business rules** (Dispatch'r, Supervisor, Observer, Scenario).
- [ ] **SLA reminders/escalations** and **CSAT surveys**.
- [ ] **"Reopen on customer response."**
- [ ] **Parent-child ticketing** (re-enable after).
- [ ] Pre-create `created_date_custom_field` (text) if you're preserving original dates.

> The zero-email guarantee = target email OFF (this step) **plus** notes-mode. Both. The tool cannot flip these for you.

## 2. Configure
```bash
cp config.production.yaml config.yaml     # edit the two domains + agent map
$env:FD_SOURCE_API_KEY = "..."            # keys via env vars, never in the file
$env:FD_TARGET_API_KEY = "..."
```

## 3. Gate 1 — Pre-flight (no writes)
```bash
python -m fdmigrate check   --config config.yaml
python -m fdmigrate run     --config config.yaml --dry-run --state-dir ./_dry
```
- [ ] `check`: both accounts OK, **AGENT GAP = 0** (fix in mapping.agents or create agents), **value coverage clean**, and note the **archived-tickets** line.
- [ ] If archived feature is ON → set `tickets.archived_scan: true` + `archived_id_max`.
- [ ] `--dry-run`: no errors building payloads. (Ignore its contact/company "created" counts — dry-run over-reports those; ticket counts are accurate.)

## 4. Gate 2 — Sample (real writes, small)
```bash
python -m fdmigrate run    --config config.yaml --only tickets --limit 50
python -m fdmigrate verify --config config.yaml --deep
```
- [ ] `verify_deep.csv`: **100% clean** on the sample. If not, stop and read the mismatched fields.

## 5. Full run (resumable — safe to Ctrl-C and re-run)
```bash
python -m fdmigrate run --config config.yaml
```
- [ ] Runs in dependency order; watch the heartbeat (rate/min + ETA). If it dies, just re-run — it resumes from the checkpoint, no duplicates.
- [ ] For huge jobs, run tickets in batches with `tickets.filters` (per status/date window) and reconcile each batch against a source **View** count.

## 6. Gate 3 — Certify completeness
```bash
python -m fdmigrate verify --config config.yaml --deep --completeness ticket,contact,company
```
- [ ] Completeness: **PASS** for each entity (MISSING = 0). Archived is separate — only covered if `archived_scan` ran.
- [ ] `failures.csv` empty (or every row a documented, accepted exception).
- [ ] `attachment_manifest.csv`: every non-migrated attachment has an acceptable reason.
- [ ] If failures exist: `python -m fdmigrate retry --config config.yaml`, then re-verify.

## 7. Cutover
```bash
python -m fdmigrate run --config config.yaml --delta   # catch anything changed since the main run
```
- [ ] Delta pass clean.
- [ ] **Re-enable** the target notifications/automations/SLA you disabled in step 1.
- [ ] Hand the client the `verify_deep.csv` + `completeness.csv` as the sign-off report.

---
### If something goes wrong
- **Tickets failing on one field:** `check` should have caught value gaps; see `failures.csv` for the field, add the mapping, `retry`.
- **Need to re-migrate an entity from scratch:** `reset --entity <name>` (marker tags still prevent duplicates).
- **Unsure a config is safe:** always `--dry-run` first.
- **A customer got an email:** a target email path was still ON in step 1. Stop, fix the setting, and note that the tool did not send it — a target automation/notification did.
