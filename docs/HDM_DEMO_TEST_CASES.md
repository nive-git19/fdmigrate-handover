# HDM demo — test cases for tickets #47–#56

Validation for the Help Desk Migration demo run, **US → EU**
(`sdkinfinity8689` → `sdkinfinity-help`).

Every expected value below was read off the live source after seeding, so each
check is a comparison against a known number — not a judgement call.

**Demo selection string:** `47,48,49,50,51,52,53,54,55,56`

---

## The source dataset, measured

| | Expected on target |
|---|---|
| Tickets | **10** |
| Conversations | **25** — 8 incoming (customer), 5 private (internal) |
| Attachments | **5** — **4 on descriptions, 1 on a note** |
| CC addresses | **5** — 2 on #48, 3 on #55 |
| Time entries | **3** — one each on #48, #49, #54 |
| `cf_reference_number` values | **6** — #47, #48, #49, #51, #53, #56 |
| Groups | **3** (target currently has **0**) |
| Statuses | all **6** |

---

## D — before you run the demo

| ID | Check | Pass |
|---|---|---|
| **D1** | Target notifications, automations and SLA escalation **OFF** | confirmed in the EU admin UI. HDM's own guidance says the same. Requesters are `@example.com`, so nothing reaches a real person — but bounce volume can trip Freshdesk's outgoing-email block mid-demo |
| **D2** | Agent auto-match | both accounts hold the same 2 agents by email. **Auto-match should go green without a manual pick** — if it doesn't, the addresses differ and you'll be assigning by hand in front of an audience |
| **D3** | Group mapping | target has **0 groups**. HDM offers *"Add the same group on Freshdesk"* — this is a capability our tool doesn't have, so it's worth showing deliberately rather than skipping past |
| **D4** | `cf_reference_number` exists on target | it does. If it were missing, HDM's *"Add the same field on Freshdesk"* is the other thing worth demoing |

---

## R — the demo result table

| ID | Check | Pass |
|---|---|---|
| **R1** | Tickets: Available / Migrated / **Failed** / **Skipped** | 10 migrated, **0 failed, 0 skipped** |
| **R2** | Contacts and Companies also migrated | 8 demo contacts, 3 demo companies |
| **R3** | Download reports | saved. This is the client-facing artefact — get it before the demo link expires |

---

## F — field fidelity, per ticket

Spot-check all ten; these are the ones that carry risk.

| ID | Ticket | Check | Expected |
|---|---|---|---|
| **F1** | #53 | **Unicode subject survives** | exactly `配送状況の確認 — order #HW-4471 (retour à l'expéditeur)` — CJK, em-dash, and the accented `à`/`é` all intact. Compare the rendered string, not its length |
| **F2** | #53 | Status not collapsed | **Waiting on Customer**, not Open |
| **F3** | #56 | Status not collapsed | **Waiting on Third Party**, not Open |
| **F4** | #54 | Source preserved | **Phone** — the only phone-sourced ticket |
| **F5** | #47/#48/#49/#51/#53/#56 | Reference numbers | `100241`, `100242`, `100245`, `100243`, `100246`, `100250` |
| **F6** | #48, #55 | CC lists | 2 and 3 addresses respectively — **5 total** |
| **F7** | all | Priority + type spread | 4 priorities, 4 types, unchanged |
| **F8** | #54 | Phone-only requester | Aoife Byrne arrives **with no email address**. Contact dedup usually keys on email; there isn't one here |

---

## C — conversations and attachments

The expensive failures live here.

| ID | Check | Expected |
|---|---|---|
| **C1** | **Private notes stay private** | **5** private notes — #47×1, #48×1, #49×2, #56×1. Every one still internal. *A single leaked internal note in front of a client ends the demo* — check this first |
| **C2** | Conversation counts per ticket | #47=4, #48=3, #49=**6**, #50=2, #51=2, #52=1, #53=2, #54=1, #55=1, #56=3 — **25 total** |
| **C3** | **Attachment on a note, not the description** | #51 carries `account-export-sample.csv` (297 B) on a **conversation**. Tools that only read `ticket.attachments` silently drop it. This is the single most likely quiet failure in the set |
| **C4** | Description attachments | #47 `sso-error-screenshot.png` 1,798 B · #48 `invoice-INV-2026-0871.pdf` 1,154 B · #49 `api-error-log.txt` 761 B · #50 `reset-flow-notes.docx` 1,120 B. **Open each one** — a file that downloads but won't render is a corrupt migration that reconciliation scores as a pass |
| **C5** | Message direction | **8 incoming** customer messages still attributed to the customer, not flipped to agent. HDM claims to preserve this; our own tool flattens it in notes-mode, so it's a real difference worth seeing |

---

## K — what to watch for, and one open question

| ID | Item | Why it matters |
|---|---|---|
| **K1** | **Created / Updated / Closed dates** | **The interesting one.** HDM maps these as system fields, but the Freshdesk API rejects back-dating on ticket create — proven live, 28 Aug. So either HDM has a partner API we don't, or the dates quietly become the migration date. `HDM_REFERENCE.md` gap #5 flags this as unverified. **Check the created date on #47 against the source.** Whichever way it lands, you learn something you currently don't know |
| **K2** | Time entries | 3 on the source. HDM migrates them; our tool does not. Confirm they arrive — it's the clearest capability difference in the set |
| **K3** | The `imported` tag | HDM tags migrated tickets `imported` by default. Expect `zzz-demo-10` **plus** `imported` on the target |
| **K4** | Original ticket IDs | will **not** be preserved — target ids continue that account's own sequence. True of every tool including ours; say it before someone notices |

---

## Before the demo

The EU target already holds **30 unrelated tickets**. The result table will read
cleanly either way, but the ticket list won't — 10 clean arrivals demo
considerably better than 40 mixed. Wipe it first if the audience will be
looking at the list:

```bash
python seed/cleanup_tickets.py --yes      # target only; previews without --yes
```

---

## Sign-off

| ID | Check | Result | Notes |
|---|---|---|---|
| D1 | Target prepared | | |
| D2 | Agent auto-match | | |
| D3 | Group mapping | | |
| D4 | Custom field present | | |
| R1 | 10 migrated, 0 failed | | |
| R2 | Contacts + companies | | |
| R3 | Reports downloaded | | |
| F1 | Unicode subject | | |
| F2 | Waiting on Customer | | |
| F3 | Waiting on Third Party | | |
| F4 | Phone source | | |
| F5 | Reference numbers ×6 | | |
| F6 | CC lists ×5 | | |
| F7 | Priority + type spread | | |
| F8 | Phone-only requester | | |
| C1 | **Private notes private ×5** | | |
| C2 | 25 conversations | | |
| C3 | **Note attachment on #51** | | |
| C4 | 4 description attachments open cleanly | | |
| C5 | 8 incoming still incoming | | |
| K1 | **Created dates preserved?** | | resolves an open question |
| K2 | Time entries ×3 | | |
| K3 | `imported` tag | | |
| K4 | IDs not preserved | | expected |
