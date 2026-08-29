# Migration fidelity spec

What a paid tool measurably delivers, what the Freshdesk API physically allows,
and the gap between them — which is the work.

Everything here was measured live on **29 Aug 2026** against the US→EU trial
pair. No claim in this document comes from documentation; each one comes from a
request and its response.

---

## 1. The reference: what HDM actually did

Help Desk Migration ran a demo migration of **20 tickets**, US → EU. Diffing
every pair field by field:

| Behaviour | Result | Verdict |
|---|---|---|
| `created_at` preserved | **20/20** | **HDM can back-date. We cannot** — see §2.1 |
| Agent authorship on conversations | **preserved** — notes resolve to a real target *agent* | **HDM can. We cannot** — see §2.2 |
| Private notes stay private | 20/20 | we match |
| Requester matched by email | 20/20 | we match |
| Responder matched by email | 20/20 | we match |
| Status / priority / type / source | **20/20** | we match |
| `incoming` flag on customer messages | **0/20 — flattened to false** | **HDM loses this too** |
| Ticket description | **replaced by the subject line**; the real description became the first conversation | **HDM loses this. We don't** |
| `due_by` | dropped, or reset to migration time by the target's SLA | neither preserves it |
| Tags | adds `imported` (HDM's default) plus `Helpdesk migrated` (typed into the *Add a new tag to tickets* option by us) | cosmetic |

One target ticket now reads `Pending` where the source reads `Open`. That is a
manual change made on our side after the migration, to observe SLA behaviour on
open tickets — not something HDM did.

### What that run did *not* prove

The 20 selected tickets happened to contain **zero attachments, zero custom-field
values, zero CCs, zero groups and zero time entries**. So HDM's handling of all
five is still unverified — the demo's green result table says nothing about them.

That is exactly why the new datasets exist. Re-run a demo selecting `47-56`
(realistic) or `74-88` (deep) and those five get exercised for the first time.

### The honest read

Two genuine capability gaps — dates and agent authorship. Everything else is
either parity or a place where **our tool is already better**: we keep the
description as the description; HDM demotes it to a reply and puts the subject
in its place.

Notes-mode flattening `incoming` has been carried as a limitation in our
validation report. HDM does the same thing. It is industry-normal, not a
competitive weakness — stop apologising for it, just disclose it.

---

## 2. Platform findings — proven live, 29 Aug

These constrain any tool built on the public v2 API, ours included.

### 2.1 Back-dating is impossible via the public API

```
POST /tickets           + created_at    → 400 invalid_field
POST /tickets           + updated_at    → 400 invalid_field
POST /tickets           + imported_at   → 400 invalid_field
PUT  /tickets/{id}      + created_at    → 400 invalid_field
PUT  /tickets/{id}      + updated_at    → 400 invalid_field
POST /tickets/{id}/notes+ created_at    → 400 invalid_field
```

Every route rejected. Yet HDM preserved dates on 20/20 tickets, so **HDM is not
using the public v2 API for ticket creation.** They are a Freshworks Marketplace
partner; the reasonable inference is privileged or partner API access.

This is not a coding problem. It is a **commercial one** — see fix P0-1.

### 2.2 One API key cannot write as another agent

```
POST /tickets/{id}/notes  {user_id: <other agent>}  → 403 invalid_user
    "You are not authorized to perform this action on behalf of this user"
```

Refused for `private`, `public` and `incoming` variants alike. The key used is
an **Account Administrator with Global Access** (`ticket_scope: 1`) — which is
precisely the prerequisite HDM's own wizard states. So the permission ceiling
is not the explanation.

Consequence: **a migration driven by a single API key attributes every agent
message to the key's owner.** A 25-agent history collapses to one name.

Only two ways out, both imperfect:

- Collect **one API key per agent** and post each agent's messages with their
  own key. Freshdesk shows an API key only in that agent's own profile, so this
  needs every agent's cooperation. Workable at 3–5 agents; not at 25.
- Obtain partner API access (same conversation as P0-1).

Contacts are unaffected — `user_id` pointing at a *contact* works fine, which is
why customer messages carry correct authorship and agent messages don't.

### 2.3 `include=conversations` silently truncates at 10

```
GET /tickets/88?include=conversations   → 10 conversations
GET /tickets/88/conversations (paged)   → 40 conversations
```

**30 messages invisible**, HTTP 200, no warning. A tool that reads the embed
loses 75% of a long thread and reports success. Reconciliation that counts
using the same embed agrees with itself and still misses them.

And the dedicated endpoint defaults to `per_page=30` — so even that truncates a
40-message thread unless you paginate explicitly.

> `fdmigrate` already paginates `/tickets/{id}/conversations` at `per_page=100`
> (`client.py:34`, `phases/tickets.py:342`). **Not a bug in our tool** — but it
> is the first thing to check in any other implementation, and the reason
> ticket #88 exists.

### 2.4 A responder not in the assigned group is silently cleared

```
PUT /tickets/74  {responder_id: <agent>, group_id: <group agent isn't in>}
    → 200 OK,  responder_id afterwards: null
```

Then, after adding that agent to the group:

```
PUT /tickets/74  {same payload}   → 200 OK,  responder_id: <agent>  ✓
```

So **group membership must be migrated before ticket assignment**, or every
assignment silently drops while the API reports success. The ticket looks
migrated; it is unassigned.

Compounding it: `GET /groups` returns `agent_ids: None` for every group. Only
`GET /groups/{id}` returns real membership.

```
Billing & Accounts   list=None  detail=[]
Escalations          list=None  detail=[158018755200]
```

> `fdmigrate` already reads membership from the detail endpoint and **merges**
> rather than overwrites (`phases/groups.py:50-71`). Also not a bug in our tool.

### 2.5 Smaller ones

| Finding | Consequence |
|---|---|
| `cc_emails` is **create-only** — `PUT` returns 400 `invalid_field` | the full CC list must go on the create call; it cannot be corrected afterwards |
| Freshdesk **strips `data:` URI images** from bodies | inline images must be hosted attachments; a data URI is silently removed |
| `unique_external_id` **is accepted** on create | a cleaner dedup key than marker tags — worth considering as a second layer |
| Deleting a contact **permanently reserves its phone number** | close contacts that have phones; only delete phone-less ones |

---

## 3. The fix list

Priority is by client impact, not effort.

### P0 — changes what we can promise

| # | Fix | Notes |
|---|---|---|
| **P0-1** | **Resolve the date question with Freshworks.** Ask the CSM directly how HDM preserves `created_at`, and whether partner/ISV API access is available to us | Not a coding task. It is the single biggest fidelity gap and it is decided in a conversation, not an editor. Until answered, every proposal must say migrated tickets carry the migration date, with the original in a custom field |
| **P0-2** | **Decide the agent-authorship position.** Either collect per-agent API keys, or disclose that agent messages land under one name | Must be settled before any client sees a migrated ticket. It is very visible and cannot be fixed after the fact |
| **P0-3** | **Run custom-field VALUE migration** against `#84` / `#47` | Risk R-C. Code exists, has never seen data. Now it can — this is a run, not a build |

### P1 — real feature gaps against HDM

| # | Fix | Test data |
|---|---|---|
| **P1-1** | Per-value mapping for **Type / Source / Priority / Group** — we only map Status today | `#47-56` span 4 types, 3 sources, 4 priorities, 3 groups |
| **P1-2** | **Create missing custom fields** on the target instead of requiring pre-creation | `#84` |
| **P1-3** | **Time entries** — HDM migrates them, we drop them | `#83` (two agents), `#48`, `#54` |
| **P1-4** | **Inline images.** Bodies reference region-specific CDN URLs (`freshdeskusercontent.com` vs `-euc`). Migrate them as attachments and rewrite the `src`, or every inline image 404s once the source is decommissioned | design requirement — data URIs are stripped, so this is the only route |
| **P1-5** | **Create agents on target** + a default-agent fallback for unmatched ones | `check` flags gaps today but cannot fix them |
| **P1-6** | Ensure **group membership migrates before ticket assignment** | §2.4 — already handled; add a regression test so it stays that way |

### P2 — after the above

| # | Fix |
|---|---|
| **P2-1** | Archived-ticket ingestion (risk **R-A**) — needs a paid Pro account, cannot be closed on trials |
| **P2-2** | Throughput / concurrency (risk **R-B**) — trials cap at 50 calls/min vs Pro's ~400 |
| **P2-3** | Richer company and contact fields (health score, tier, renewal date, address, title, about) |
| **P2-4** | Product / Brand mapping |
| **P2-5** | Merged and parent-child tickets — no v2 API to create them, so document as unsupported |

---

## 4. The deep dataset — `#74`–`#88`

`seed/seed_deep.py`, tagged `zzz-deep`. Measured after seeding:

**69 conversations · 27 incoming · 12 private · 10 attachments · 1,586,901 bytes**

| Case | # | What it proves | Expected |
|---|---|---|---|
| **D01** | 74 | Cross-agent handoff with signatures and a growing quoted chain | 6 conv, 2 incoming, 2 private, 2 attachments, blockquote nested **3 deep**, 4 signature blocks, legal disclaimer, group + responder change mid-thread |
| **D02** | 75 | Internal consult — two agents talking privately | 4 conv, **2 private** |
| **D03** | 76 | **Attachments in every position** | **6 files**: 2 on description, 1 on a customer reply, 1 on an agent reply, 1 on a **private note**, and `screenshot-error.png` **twice under the same name** |
| **D04** | 77 | Unicode filename + large file | `отчёт-2026-Q3_报告.pdf` and a **1,573,020-byte** zip |
| **D05** | 78 | Inline `data:` image | **Freshdesk stripped it** — the finding *is* the result |
| **D06** | 79 | Forwarded mail with headers in the body | From/Sent/To block intact |
| **D07** | 80 | Multi-address CC | 3 addresses, set at create (create-only field) |
| **D08** | 81 | Description only | **0 conversations** — the empty-thread edge case |
| **D09** | 82 | Full status journey | ends **Closed**, 5 conv, passed through Pending / Waiting on Customer / Waiting on Third Party |
| **D10** | 83 | Time logged by two agents | **3 time entries**, 01:15 + 02:30 + 00:20 |
| **D11** | 84 | Every custom field populated | `cf_reference_number = 200111` — risk R-C |
| **D12/13** | 85, 86 | Cross-referenced duplicate pair | each has a private note naming the other |
| **D14** | 87 | Agent as requester | requester is also an agent |
| **D15** | 88 | **40-message thread** | **40 conversations, 20 incoming, 5 private.** The embed shows 10 — see §2.3 |

### Known limitation of this dataset

Cross-agent authorship could not be seeded: 7 messages intended for the second
agent were refused with `403 invalid_user` (§2.2) and posted as the key owner
instead. The seeder reports each downgrade.

**For genuine cross-agent test data, use `#20`–`#39`** — the older `zzz-ready-test`
set already carries notes authored by a second agent, and HDM demonstrably
preserved that attribution. Those are the tickets to test agent authorship against.

---

## 5. What "solid" means

The tool is solid when, on `#47-56` and `#74-88`:

1. Every count reconciles — 69 conversations, not 39; 10 attachments, all opening cleanly
2. All 12 private notes are still private
3. Custom-field values arrive (P0-3)
4. Every ticket keeps its responder **and** its group
5. Type / Source / Priority map by value, not just Status (P1-1)
6. Time entries arrive (P1-3)
7. Inline images still render after the source is gone (P1-4)
8. The two things we **cannot** do — original dates and per-agent authorship —
   are stated in the proposal rather than discovered by the client

Points 1–7 are engineering. Point 8 is the one that decides whether a delivery
goes well, and it is settled before any code is written.
