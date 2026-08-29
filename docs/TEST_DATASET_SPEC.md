# Test dataset spec — what the data has to contain to prove anything

A migration tool passes easy data by definition. This is the list of cases that
actually break migration tools, why each one breaks them, and where each is
covered today.

Coverage was measured live on the shared pair on **28 Aug 2026** — not read off
the docs. See §4 for what that measurement found.

Legend: **✅ covered** by an existing seeder · **🆕 new** in `seed/seed_edge_cases.py`
· **⛔ impossible** on a trial plan.

---

## 1. Why the current dataset proves less than it looks

The pair has 30 source tickets. Measured:

| Dimension | Source account today | Verdict |
|---|---|---|
| Groups | **0** | group mapping is a dead path |
| Custom-field values | **0** of 30 tickets | **risk R-C untested** |
| Solution categories (KB) | **0** | `knowledge_base` phase never runs |
| Canned response folders | **0** | `canned_responses` phase never runs |
| Ticket sources present | 2 of 11 (Email, Portal) | no phone/chat/widget |
| Statuses present | 4 of 7 | no *Waiting on Customer / Third Party* |
| Longest conversation | **3 messages** | no pagination inside a thread |
| Attachments | 2 files, **70 B and 60 B** | size/type/MIME paths untested |
| Created-date span | 2 days | date windowing untested |
| Requester addresses | all `@example.com` | nothing is deliverable |

Three of the eight migration phases (`groups`, `canned_responses`,
`knowledge_base`) currently migrate **zero objects**. A green run across them is
not evidence — it is an empty loop.

One case is worse than untested: `[RMATT-003] Attachment on a note` carries **no
attachment at all** on either side. It reports PASS because nothing was compared.

---

## 2. The complexity catalogue

### A. Text and encoding — where silent corruption hides

| # | Case | What it breaks | Cover |
|---|---|---|---|
| E01 | Emoji + CJK + Greek in subject *and* body | double-encoding, `?????` subjects, MySQL `utf8` vs `utf8mb4` truncation | 🆕 |
| E02 | Right-to-left (Arabic, Hebrew) | `dir` attribute stripped; text reverses | 🆕 |
| — | Zero-width space, `&amp;`, `<tag>` in body | double-escaping on each hop — `&amp;amp;` | 🆕 (in E01) |
| E08 | HTML body: tables, inline styles, `<blockquote>` quoted reply, signature | sanitiser strips markup; quoted history collapses | 🆕 |
| E09 | ~90 KB description | silent truncation at an undocumented cap | 🆕 |

### B. Conversation structure — the most expensive thing to get wrong

| # | Case | What it breaks | Cover |
|---|---|---|---|
| E03 | 30-message thread | conversation pagination (`per_page` default 30 — a 31st message is the classic off-by-one) | 🆕 |
| — | Mixed incoming / outgoing / private in one thread | ordering by id vs `created_at`; privacy flip | ✅ pilot |
| — | Private note that must **stay** private | a leaked internal note is the single worst failure mode | ✅ pilot (V9) |
| — | Attachment on a reply, not the ticket | tools that only read `ticket.attachments` | ✅ pilot |
| — | Attachment on a **note** | same, on the other branch | ❌ **claims coverage but the file is missing — fix this** |

### C. Identity — requester and agent edge cases

| # | Case | What it breaks | Cover |
|---|---|---|---|
| E07 | Unicode requester name | contact matching on a normalised name | 🆕 |
| E12 | Contact with email, **no name** | `name` is required on create → 400 | 🆕 |
| E13 | Contact with `other_emails` aliases | matching on primary only → duplicate contacts | 🆕 |
| E14 | Phone-only contact, **no email** | email is the usual dedup key; there isn't one | 🆕 |
| E10 | Requester who is also an agent | creates an agent as a contact, or fails outright | 🆕 |
| E06 | No responder **and** no group | `None` handling on both fields | 🆕 |
| — | Source agent with no target counterpart | pre-flight must catch it, not the run | ✅ (V3) |

### D. Field values — risk R-C

| # | Case | What it breaks | Cover |
|---|---|---|---|
| E05 | Every custom field carrying a real value | **the open residual risk.** Code path exists, has never seen data | 🆕 |
| E16 | Status *Waiting on Customer* (6) | non-default statuses need explicit mapping | 🆕 |
| E17 | Status *Waiting on Third Party* (7) | same | 🆕 |
| E18 | All 6 accepted `source` values | Chat (7) and Feedback Widget (9) reject on create in some plans | 🆕 |
| E19 | Group assignment | dead path today — no groups exist | 🆕 |
| E20 | `due_by` / `fr_due_by` set | SLA timer absent on the target status → create fails (this is exactly what V10 fixed) | 🆕 |
| E21 | Time entries | not migrated by our tool — HDM does. Confirm it is a *known* drop, not a silent one | 🆕 |

### E. Records that must **not** migrate

| # | Case | What it breaks | Cover |
|---|---|---|---|
| E22 | Deleted ticket | soft-deleted rows still returned by some endpoints → resurrected on the target | 🆕 |
| E23 | Spam ticket | same, and worse — spam reappearing in a live queue | 🆕 |

Both are counted in the *"N in = N out"* reconciliation. If the tool migrates
them, completeness still reads green while the target is wrong. **Reconciliation
alone cannot catch this — it needs an explicit assertion.**

### F. The real-mailbox loop — the only way to prove Limitation #5

| # | Case | What it proves | Cover |
|---|---|---|---|
| E24 | 5 tickets whose requester is a **real mailbox you own** | zero-email during migration, and post-migration continuity | 🆕 |

Validation report Limitation #5 reads: *"Zero-email guarantee is
mechanism-verified, not inbox-verified… I cannot read the customers' inboxes to
prove silence."* A real mailbox is the missing oracle — it turns an argument
into evidence. Procedure is test **E-1 / E-2 / E-3** in `PRE_MIGRATION_TEST_CASES.md`.

### G. Cannot be covered on this pair

| Case | Why | Consequence |
|---|---|---|
| Archived tickets | `403 require_feature` on both accounts | **risk R-A stays open** |
| Real throughput | 50 calls/min vs Pro's ~400 | **risk R-B stays open**; every ETA from this pair is ~8× pessimistic and meaningless |
| Real inbound email threads | needs mail sent to `support@…freshdesk.com` from real mailboxes | approximated by E24 |
| Back-dated `created_at` | Freshdesk API rejects it | provenance banner is the workaround, by design |
| Merged / parent-child tickets | no v2 API to create them | flag as untested if the client uses them |

---

## 3. What "real requester replies" actually means here

There are three fidelity levels. Know which one you are buying.

**Level 1 — API-seeded conversation.** `POST /tickets/{id}/notes` with
`incoming: true` and the contact's `user_id`. This is the *only* API route that
produces a customer-side message, and it is what `seed_pilot_100.py` uses. Good
enough for structure and ordering. It does **not** produce real mail headers,
quoted history or threading `Message-ID`s.

**Level 2 — real inbound mail.** Send a genuine email from a real mailbox to
`support@sdkinfinity-help.freshdesk.com`. Freshdesk creates a `source: 1` ticket
with authentic headers, then a real reply from the same mailbox threads onto it.
This is the highest fidelity available and costs nothing — see test **E-4**.

**Level 3 — real reply on a *migrated* ticket.** The one that matters most and
has never been tested: after migration, does the target ticket still work as a
live ticket? Agent replies → customer receives → customer replies → does it
thread back onto the migrated ticket, or fork a new one? See test **E-3**.

Level 3 is the difference between "the data arrived" and "the helpdesk works."
No amount of reconciliation CSV tells you this.

---

## 4. Running the seeders

Order matters — `seed_edge_cases.py` creates the groups and contacts the others
reference.

```bash
export FD_SOURCE_DOMAIN=https://sdkinfinity-help.freshdesk.com
export FD_SOURCE_API_KEY=...
export FD_CANARY_EMAIL=you@gmail.com          # optional, enables E24

python -X utf8 seed/seed_edge_cases.py        # ~30 tickets + groups/KB/canned
python -X utf8 seed/seed_pilot_100.py         # 100 tickets, attachments, threads
python -X utf8 seed/seed_bulk.py 1000         # volume, only if you want it
```

Every seeder tags its output (`zzz-edge`, `zzz-pilot-100`, `zzz-bulk-1k`) so
`seed/cleanup_tickets.py` can find it again. `seed_edge_cases.py` writes
`seed/_edge_manifest.json` with every id it created — that file is the input to
the post-migration assertions.

**Before running any of them:** turn the source account's outbound email OFF,
unless you are deliberately running the canary. Seeded requesters are fake
addresses and the bounce volume can trip Freshdesk's outgoing-email block.

Anything that fails on a trial plan is printed and skipped, not fatal. Record
what skipped — that list is itself a finding.
