# Help Desk Migration (HDM) — Observed Behavior Reference
Captured live 2026-06-23 by driving HDM's wizard (Freshdesk→Freshdesk) on the new
trial pair: source `sdkinfinity1684.freshdesk.com` → target `oskloud-supportdesk.freshdesk.com`.
Migration id `3BA32AEEDD6126`. Screenshots saved alongside this file (`hdm-*.png`).

This is "what a paid tool actually does" — used as the spec/oracle for our own tool.

---

## The 5-step wizard
`Migrate from` → `Migrate to` → `Choose objects` → `Demo migration` → `Full migration`
- Connect = platform + URL + API key per side. Prereq shown: "Account Administrator role and Global Access scope"; API key visible in profile settings only after email verified.
- Steps lock once the demo runs.

## Objects offered (Freshdesk→Freshdesk)
- **Help Desk:** Agents, Companies, Contacts, Tickets
- **Business Rule:** Canned Responses
- **Knowledge Base:** Categories, Folders, Articles
- **NOT offered as objects:** Groups, SLA policies, automations/business rules, time-tracking-as-object (time logs are a ticket field). → same scope boundary as our tool.

---

## TICKET field mapping  (17 fields; "10 of 17" auto-mapped)  [hdm-ticket-mapping-full.png]
**System fields — always included, auto-mapped, greyed/uneditable:**
Subject, Tags, CC, Agent, Contact, Comments (=conversations), **Time logs**, **Created date**, **Updated date**, **Closed date**

**Mappable fields — per-VALUE dropdowns (map each source value → a target value):**
- Group (e.g. Unassigned→Unassigned, Test Group→Unassigned)
- Type (Question/Incident/Problem/Feature Request/Refund)
- Source (Email, Portal, Phone, Forum, MobiHelp, Feedback Widget, Outbound Email, Ecommerce, Whatsapp, Web Chat, Web Form, Instagram Msg/Comment, Facebook Msg/Comment, Mobile Chat SDK, SMS)
- Status (Open, Pending, Resolved, Closed, Waiting on Customer, Waiting on Third Party, Assigned to AI Agent)
- Priority (Low/Medium/High/Urgent)
- **Product (Brand)** — mapped by value
- **Reference Number** — a custom field

**Mapping capabilities (the paid-tool extras):**
- **Create field on target:** "Choose field to create" + **"Add the same field on Freshdesk"** = one-click create a missing custom field on the target, then reload.
- **Per-value mapping** for every select field, with **"Use for empty values"** default and **"Skip this field"**.
- Understands field **types**: text, number, decimal, select, date, boolean, array, object, collection (and what each maps to).
- "The Migration Wizard will only migrate mapped fields."

## TICKET Options (add-ons; free during demo)  [hdm-ticket-options.png]
Migrate time entries · Migrate the newest records first · Select records for Demo (by ID) · Skip attachments · **Migrate inline images as attachments** (default ON) · **Add a new tag to tickets** (default ON, tag "imported").

## TICKET Filters  [hdm-ticket-filters.png]
"Filters use AND logic (all conditions must be met)… fields provided by the Source API." Custom filters on request.

---

## COMPANY field mapping  [hdm-company-mapping.png]
Name, Description, Domains, Notes, Health score (At risk/Doing okay/Happy), Account tier (Basic/Premium/Enterprise), Renewal date, Industry (full GICS-style list) + creatable custom fields (e.g. "Listed Company").
**Note: "Existing Companies on the Target will not be updated."** (skip — same as ours)

## CONTACT field mapping  [hdm-contact-mapping.png]
Name, Email, Company, Phone, Title, Mobile phone, Address, Time zone (full GMT list), Language (full list), Tags, About + creatable custom fields (e.g. "VIP Customer").

## AGENT matching  [hdm-agent-matching.png / hdm-demo-start.png]
Triggered at demo start ("It looks like Agents is missing on the Target.").
- **Create agents on target:** "Add the same agent(s) on Freshdesk".
- **Default agent** for unassigned + deleted/inactive agents, with a checkbox to auto-assign all unmatched.
- Per-agent source→target dropdowns; **Auto-match** button; yellow = not matched. (Our run: niveditha@sdkinfinity.com auto-matched; dinesh@sdkinfinity.com→dinesh@oskloud.com.)

---

## DEMO MIGRATION result  [hdm-demo-complete.png]
"Generating migration preview… up to 5 minutes." Writes a small sample into the REAL target. Result table columns: **Available | Migrated | Failed | Skipped** per object.
Our demo: Agents 3, Companies 3, Contacts 5, Tickets 6 — **0 failed, 0 skipped.**
Post-demo actions: **Download reports**, **Rollback Demo**, **Checkout** (pay for full), **Start Full Migration**.

## HDM's Freshdesk-specific guidance (matches our findings)
"Turn off automation rules and parent-child ticketing in Freshdesk and keep them disabled during migration."

---

## GAP ANALYSIS — HDM vs our tool

### HDM does, we DON'T (yet):
1. **Create custom fields on the target** (one-click) — we require pre-creating them.
2. **Create agents on target + default-agent fallback** — we deliberately excluded (match-only). Reconsider only if user wants it.
3. **Per-value mapping for ALL picklists** (Type/Source/Priority/Group) — we only do Status (by label).
4. **Time logs** migration.
5. **Created/Updated/Closed date** as mapped fields (we use a provenance banner; verify whether Freshdesk actually honours HDM's dates).
6. **Product/Brand** mapping (V1 Odoo did by-name; standalone currently drops+logs).
7. **Contact Address/Title/About**, richer Company fields (health/tier/renewal/industry — V1 had these; confirm in standalone).
8. **Rollback** of a migration.
9. Add-on toggles: skip-attachments, inline-images-as-attachments, tag-migrated-tickets.

### We have, comparable or better:
- **Resumable + idempotent + dedup** (marker tags + SQLite) — proven on 1000 tickets incl. pause/resume.
- **Reconciliation CSVs** (failures, attachment manifest, spot-check).
- **Match-wise filtering** (status/group/tag/date/etc.) — just built.
- **Private-notes default** so migration never emails customers.
- Provenance banner preserving original dates/author when the API can't.

### Same scope boundary as HDM:
No Groups/SLA/automations/business-rules as migratable objects.
