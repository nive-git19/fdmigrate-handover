# Production Migration — Freshdesk → Freshdesk

Clean, production-only copy of the migration tool. All scratch scripts, throwaway
databases, and stale comparison docs from the old working folder are intentionally
left out.

## Layout
```
Production Migration/
  fdmigrate/               the tool (Python package) — the only code that runs
  config.production.yaml   copy this to config.yaml and fill in your two accounts
  config.example.yaml      original annotated example (reference)
  requirements.txt         Python dependencies
  Dockerfile               containerized run
  docs/
    OPERATOR_QUICKSTART.md        ← RUN A CLIENT: one-page top-to-bottom checklist
    GO_LIVE_VALIDATION_REPORT.md  what's been validated + go/no-go verdict
    PRODUCTION_RUNBOOK.md         what YOU must do/decide to go live (full detail)
    PRODUCTION_READINESS_REVIEW.md full assessment + 65/100 score + gap list
    MIGRATION_GUIDE.md            pre-flight (disable notifications/automations/SLA)
    HDM_REFERENCE.md              Help Desk Migration behavior (our spec/oracle)
    SUGGESTIONS.md                engineering roadmap (S-xx items)
    HANDOFF.md                    working handoff / current state
```

## Quick start
```bash
python -m venv .venv && .venv\Scripts\activate     # Windows
pip install -r requirements.txt

# secrets (never in the file)
$env:FD_SOURCE_API_KEY = "..."
$env:FD_TARGET_API_KEY = "..."

cp config.production.yaml config.yaml               # then edit the two domains

python -m fdmigrate check  --config config.yaml     # 1. validate + pre-flight coverage
python -m fdmigrate run    --config config.yaml --dry-run --state-dir ./_dry   # 2. dry-run: zero writes
python -m fdmigrate run    --config config.yaml --only tickets --limit 50      # 3. sample gate
python -m fdmigrate run    --config config.yaml     # 4. full run (resumable)
python -m fdmigrate verify --config config.yaml --deep   # 5. reports + field-level diff + completeness
```

### Auto mode (one command, self-verifying)
```bash
python -m fdmigrate auto --config config.yaml            # gated end-to-end migration
python -m fdmigrate auto --config config.yaml --dry-run  # rehearse it with zero writes
```
`auto` runs the whole go-live sequence and STOPS itself if a gate fails:
Gate 1 connectivity → Gate 2 value coverage → foundation → **ticket sample + deep-verify
gate (won't run the full volume if the sample isn't field-clean)** → full run →
completeness + deep-verify certification (→ optional `--delta`). Fully resumable — re-run
`auto` to continue. `--sample N` sets the gate sample size; `--force` proceeds past
coverage gaps. Still requires target email OFF first (see OPERATOR_QUICKSTART step 1).

### Operability commands
```bash
python -m fdmigrate run    --config config.yaml --dry-run   # reads real; every target WRITE logged, not sent
python -m fdmigrate status --config config.yaml             # live counts from the state DB (no keys needed)
python -m fdmigrate retry  --config config.yaml [--only tickets]  # clear failed/partial + re-run only those
python -m fdmigrate reset  --config config.yaml --entity ticket   # forget an entity's state so it re-migrates
python -m fdmigrate rollback --config config.yaml            # PREVIEW: undo (delete tool-created tickets)
python -m fdmigrate rollback --config config.yaml --yes      # actually delete them
```
**rollback** deletes only tickets this tool created (by `fd-migration-*` marker); it never
deletes contacts/companies/agents (matched/updated client data). Previews unless `--yes`.
**--dry-run caveat:** ticket dedup is accurate (it uses read-only marker prefetch), but the
contacts/companies phases detect "already exists" from the target's write response, which never
fires under dry-run — so dry-run *over-reports creates* for those two. It still validates
connectivity, payload building, value coverage, and ticket coverage with zero writes.

`verify --deep` adds two certificates beyond the basic reports:
- **verify_deep.csv** — field-by-field source-vs-target diff on a ticket sample
  (subject, body, status/priority/source/type, requester, responder, group, tags,
  custom-field values, conversation count). A "clean" row is a real match.
- **completeness.csv** — re-reads the source id-space and lists any source record
  with no successful target (MISSING/partial/failed), so nothing can be silently
  dropped. Add `--completeness ticket,contact,company` to widen the pass.

## Tests
Pure-logic unit tests (stdlib `unittest`, no extra deps, no network):
```bash
python -m unittest discover -s tests -v
```
54 tests cover payload building, value/field coverage (D4), the field-level verifier
(D3), the create-path retries, store dedup/reset, archived detection (D2), and dry-run
write-suppression (D7). Run them after any change to the package.

**Before any run against real data, read `docs/OPERATOR_QUICKSTART.md`** (one-page
run checklist) and `docs/PRODUCTION_RUNBOOK.md` (decisions/inputs + go/no-go gate).
</content>
