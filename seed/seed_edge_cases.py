"""Seed the SOURCE account with the EDGE-CASE dataset.

`seed_pilot_100.py` builds the bulk/volume dataset (attachments, threads, status
spread). This one builds the *nasty* half - the cases that actually break
migration tools, plus the three phases the pilot dataset never exercises at all
(groups, knowledge base, canned responses) and the one residual risk that is
still open (R-C: custom-field VALUES).

Every case here maps to a numbered case in docs/TEST_DATASET_SPEC.md.

Usage:
    export FD_SOURCE_DOMAIN=https://yourtrial.freshdesk.com
    export FD_SOURCE_API_KEY=...
    export FD_CANARY_EMAIL=you@gmail.com      # optional; see below
    python -X utf8 seed/seed_edge_cases.py

FD_CANARY_EMAIL is a REAL mailbox you control. Five tickets are created with it
as the requester so you can use your own inbox as the oracle for the zero-email
guarantee (Limitation #5 in the validation report - currently mechanism-verified
but never inbox-verified). Leave it unset and those five are skipped.

WARNING: writes ~30 tickets plus KB/canned/group config. Throwaway accounts only.
Turn the source account's outbound email OFF before running unless you are
deliberately running the canary test.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import requests

_DOMAIN = os.environ.get("FD_SOURCE_DOMAIN", "").strip().rstrip("/")
if not _DOMAIN:
    raise SystemExit(
        "Set FD_SOURCE_DOMAIN to the account you want to SEED, e.g.\n"
        "  export FD_SOURCE_DOMAIN=https://yourtrial.freshdesk.com\n"
        "  export FD_SOURCE_API_KEY=...\n"
        "Point this at a TEST account only - it writes tickets and config.")
if not _DOMAIN.startswith("http"):
    _DOMAIN = "https://" + _DOMAIN
BASE = f"{_DOMAIN}/api/v2"
try:
    AUTH = (os.environ["FD_SOURCE_API_KEY"], "X")
except KeyError:
    raise SystemExit("Set FD_SOURCE_API_KEY (admin API key for the source account).")

CANARY = os.environ.get("FD_CANARY_EMAIL", "").strip()
TAG = "zzz-edge"
MANIFEST = Path(__file__).parent / "_edge_manifest.json"

print(f"seeding edge cases into {_DOMAIN}")
print(f"canary mailbox: {CANARY or '(none - E24 skipped)'}\n")

made: dict = {"domain": _DOMAIN, "tickets": {}, "config": {}}
_fail: list = []


def call(method: str, path: str, label: str, **kw):
    """POST/PUT/DELETE that reports and continues. Returns json | None."""
    try:
        r = requests.request(method, BASE + path, auth=AUTH, timeout=60, **kw)
    except Exception as exc:                       # noqa: BLE001
        print(f"  x {label}: {exc}")
        _fail.append(label)
        return None
    if r.status_code in (200, 201, 204):
        return r.json() if r.text.strip() else {}
    print(f"  x {label}: HTTP {r.status_code} {r.text[:160]}")
    _fail.append(f"{label} ({r.status_code})")
    return None


def get_all(path: str, **params):
    out, page = [], 1
    while True:
        r = requests.get(BASE + path, auth=AUTH,
                         params={**params, "per_page": 100, "page": page}, timeout=60)
        if not r.ok:
            return out
        b = r.json()
        out += b
        if len(b) < 100:
            return out
        page += 1


agents = get_all("/agents")
if not agents:
    raise SystemExit("no agents on the source account - create one before seeding")
AGENT = agents[0]["id"]
AGENT2 = agents[1]["id"] if len(agents) > 1 else AGENT
print(f"using agents {AGENT}, {AGENT2}\n")

# ------------------------------------------------------------------ 1. config
# Phases with NO coverage today: groups / knowledge_base / canned_responses.
print("== config (groups, KB, canned) ==")

group_ids = []
for name, desc in [("Tier 1 Support", "First line"),
                   ("Billing", "Invoices and refunds"),
                   ("Escalations", "P1 and exec escalations")]:
    g = call("POST", "/groups", f"group {name}", json={"name": name, "description": desc})
    if g:
        group_ids.append(g["id"])
        print(f"  + group {name} -> {g['id']}")
made["config"]["groups"] = group_ids
GID = group_ids[0] if group_ids else None

# Knowledge base: 2 categories -> 3 folders -> 9 articles (one draft, one rich).
kb = {"categories": [], "folders": [], "articles": []}
RICH = ("<h2>Rich article</h2>"
        "<p>Steps with <b>bold</b>, <i>italic</i> and a "
        "<a href=\"https://example.com\">link</a>.</p>"
        "<table><tr><th>Field</th><th>Value</th></tr>"
        "<tr><td>Plan</td><td>Pro</td></tr></table>"
        "<ul><li>Bullet one</li><li>Bullet two</li></ul>")
for cname, folders in [("Getting Started", ["Account setup", "First steps"]),
                       ("Troubleshooting", ["Common errors"])]:
    c = call("POST", "/solutions/categories", f"category {cname}",
             json={"name": cname, "description": f"{cname} docs"})
    if not c:
        continue
    kb["categories"].append(c["id"])
    print(f"  + category {cname} -> {c['id']}")
    for fname in folders:
        f = call("POST", f"/solutions/categories/{c['id']}/folders", f"folder {fname}",
                 json={"name": fname, "description": fname, "visibility": 1})
        if not f:
            continue
        kb["folders"].append(f["id"])
        for i in range(1, 4):
            # status 1 = draft, 2 = published. Draft articles are a classic
            # migration miss - tools that only read published silently drop them.
            status = 1 if (fname == "First steps" and i == 3) else 2
            body = RICH if i == 1 else f"<p>{fname} article {i}.</p>"
            a = call("POST", f"/solutions/folders/{f['id']}/articles",
                     f"article {fname}/{i}",
                     json={"title": f"{fname} - article {i}", "description": body,
                           "status": status, "tags": ["kb"]})
            if a:
                kb["articles"].append(a["id"])
        print(f"  + folder {fname} -> {f['id']} (3 articles, 1 draft in 'First steps')")
made["config"]["kb"] = kb

canned = {"folders": [], "responses": []}
for fname in ["General", "Billing replies"]:
    cf = call("POST", "/canned_response_folders", f"canned folder {fname}",
              json={"name": fname})
    if not cf:
        continue
    canned["folders"].append(cf["id"])
    for i in range(1, 3):
        cr = call("POST", "/canned_responses", f"canned {fname}/{i}",
                  json={"title": f"{fname} response {i}",
                        "content_html": f"<p>Hi there,</p><p>{fname} template {i}.</p>",
                        "folder_id": cf["id"], "visibility": 0})
        if cr:
            canned["responses"].append(cr["id"])
    print(f"  + canned folder {fname} -> {cf['id']} (2 responses)")
made["config"]["canned"] = canned

# ---------------------------------------------------------------- 2. contacts
print("\n== contacts ==")
contacts: dict = {}


def mkcontact(key: str, **payload):
    c = call("POST", "/contacts", f"contact {key}", json=payload)
    if c:
        contacts[key] = c["id"]
        print(f"  + {key} -> {c['id']}")
        return c["id"]
    if payload.get("email"):                     # already exists? reuse it.
        r = requests.get(BASE + "/contacts", auth=AUTH,
                         params={"email": payload["email"]}, timeout=30)
        if r.ok and r.json():
            contacts[key] = r.json()[0]["id"]
            print(f"  = {key} already exists -> {contacts[key]}")
            return contacts[key]
    return None


mkcontact("unicode", name="Zoe Muller-Odegard 陈小明",
          email="zoe.unicode.edge@example.com", job_title="Ingenieur qualite")
mkcontact("nameless", email="nameless.edge@example.com")
mkcontact("multimail", name="Multi Address", email="primary.edge@example.com",
          other_emails=["alias1.edge@example.com", "alias2.edge@example.com"])
mkcontact("phoneonly", name="Phone Only Caller", phone="+353 1 555 0134")
if CANARY:
    mkcontact("canary", name="Canary Requester", email=CANARY)
made["config"]["contacts"] = contacts

# ----------------------------------------------------------------- 3. tickets
print("\n== tickets ==")
BODY_HTML = (
    "<div><p>Hello team,</p>"
    "<p>Order <b>#A-4471</b> is showing <span style=\"color:#c00\">FAILED</span>. "
    "See <a href=\"https://example.com/orders/A-4471\">the order</a>.</p>"
    "<table border=\"1\"><tr><th>Item</th><th>Qty</th></tr>"
    "<tr><td>Widget</td><td>3</td></tr></table>"
    "<blockquote>On Mon, someone wrote:<br>&gt; original quoted text</blockquote>"
    "<p>--<br>Regards,<br>A. Customer<br>+353 1 555 0100</p></div>")


def mkticket(case: str, subject: str, **payload):
    p = {"subject": subject, "description": f"<p>{subject}</p>",
         "priority": 2, "status": 2, "type": "Question",
         "responder_id": AGENT, "tags": [TAG, case.lower()]}
    p.update(payload)
    if p.get("responder_id") is None:
        p.pop("responder_id")
    if "requester_id" not in p and "email" not in p:
        p["email"] = "edge.default@example.com"
    t = call("POST", "/tickets", f"{case}", json=p)
    if t:
        made["tickets"][case] = t["id"]
        print(f"  + {case:<8} -> #{t['id']:<5} {subject[:48]}")
        return t["id"]
    return None


def conv(tid: int, case: str, body: str, *, incoming=False, private=False, user=None):
    """Customer message = incoming note (the only API route to add one). Agent
    messages are notes too, so seeding itself never emails anybody."""
    return call("POST", f"/tickets/{tid}/notes", f"{case} note",
                json={"body": body, "private": private, "incoming": incoming,
                      "user_id": user or AGENT})


# --- E01 unicode + emoji subject and body
mkticket("E01", "Cafe naive - 日本語 - Ελληνικά - \U0001F6A8 urgent \U0001F525",
         description="<p>Unicode body: 日本語テキスト, "
                     "emoji \U0001F389\U0001F680, zero-width​ space, "
                     "ampersand &amp; &lt;tag&gt;.</p>")

# --- E02 right-to-left
mkticket("E02", "طلب إرجاع - RTL subject",
         description="<p dir=\"rtl\">هذا نص عربي.</p>"
                     "<p dir=\"rtl\">עברית לבדיקה.</p>")

# --- E03 long thread (30 messages) - exercises conversation pagination
tid = mkticket("E03", "[E03] Long thread - 30 messages", status=3)
if tid:
    for i in range(1, 31):
        conv(tid, "E03", f"<p>Message {i} of 30 in the long thread.</p>",
             incoming=(i % 2 == 1), private=(i % 5 == 0),
             user=contacts.get("unicode") if i % 2 else AGENT)
    print("             ...30 conversations added")

# --- E04 CC list
mkticket("E04", "[E04] Ticket with CC list",
         cc_emails=["watcher1.edge@example.com", "watcher2.edge@example.com",
                    "watcher3.edge@example.com"])

# --- E05 CUSTOM FIELD VALUES - residual risk R-C, untested until now
cf_fields = [f for f in get_all("/ticket_fields") if not f.get("default")]
cf_payload = {}
for f in cf_fields:
    n, t = f.get("name"), f.get("type")
    if t == "custom_number":
        cf_payload[n] = 987654
    elif t in ("custom_text", "custom_paragraph"):
        cf_payload[n] = "R-C probe value - unicode & <b>html</b>"
    elif t == "custom_checkbox":
        cf_payload[n] = True
    elif t == "custom_decimal":
        cf_payload[n] = 12.5
    elif t == "custom_dropdown":
        ch = f.get("choices") or []
        if ch:
            cf_payload[n] = ch[0] if isinstance(ch[0], str) else list(ch)[0]
if cf_payload:
    mkticket("E05", "[E05] Custom field VALUES set (closes risk R-C)",
             custom_fields=cf_payload)
    print(f"             custom_fields = {json.dumps(cf_payload)[:110]}")
else:
    print("  ! E05 skipped - no custom ticket fields defined on this account")

# --- E06 fully unassigned
mkticket("E06", "[E06] Unassigned - no agent, no group", responder_id=None)

# --- E07 / E12 / E13 / E14 requester variants
if contacts.get("unicode"):
    mkticket("E07", "[E07] Unicode requester name", requester_id=contacts["unicode"])
if contacts.get("nameless"):
    mkticket("E12", "[E12] Requester has email but no name",
             requester_id=contacts["nameless"])
if contacts.get("multimail"):
    mkticket("E13", "[E13] Requester with other_emails aliases",
             requester_id=contacts["multimail"])
if contacts.get("phoneonly"):
    mkticket("E14", "[E14] Phone-only requester, no email",
             requester_id=contacts["phoneonly"], source=3)

# --- E08 HTML-heavy body with quoted text and signature
mkticket("E08", "[E08] HTML body, quoted reply, signature", description=BODY_HTML)

# --- E09 large description
mkticket("E09", "[E09] Very large description (~90KB)",
         description="<p>" + ("Lorem ipsum dolor sit amet. " * 3500) + "</p>")

# --- E10 requester is also an agent
acontact = (agents[0].get("contact") or {}).get("email")
if acontact:
    mkticket("E10", "[E10] Requester is also an agent", email=acontact)

# --- E16 / E17 the non-default statuses
mkticket("E16", "[E16] Status: Waiting on Customer", status=6)
mkticket("E17", "[E17] Status: Waiting on Third Party", status=7)

# --- E18 every source value the account will accept
for sname, sval in [("Email", 1), ("Portal", 2), ("Phone", 3), ("Chat", 7),
                    ("Feedback Widget", 9), ("Outbound Email", 10)]:
    mkticket(f"E18-{sval}", f"[E18] Source = {sname} ({sval})", source=sval)

# --- E19 group assignment (a dead path until groups exist)
for i, g in enumerate(group_ids):
    mkticket(f"E19-{i}", f"[E19] Group assigned ({g})", group_id=g)

# --- E20 due dates
mkticket("E20", "[E20] Due dates set", priority=4,
         due_by="2027-01-31T12:00:00Z", fr_due_by="2027-01-20T12:00:00Z")

# --- E21 time entries
tid = mkticket("E21", "[E21] Ticket with time entries", status=4)
if tid:
    for mins, billable in [("01:30", True), ("00:45", False)]:
        call("POST", f"/tickets/{tid}/time_entries", "E21 time entry",
             json={"time_spent": mins, "agent_id": AGENT, "billable": billable,
                   "note": f"Worked {mins}"})

# --- E22 deleted ticket - must NOT appear on the target
tid = mkticket("E22", "[E22] DELETED - must not migrate")
if tid:
    call("DELETE", f"/tickets/{tid}", "E22 delete")
    print("             ...deleted (expect absent from target)")

# --- E23 spam ticket - must NOT appear on the target
tid = mkticket("E23", "[E23] SPAM - must not migrate")
if tid:
    r = requests.put(f"{BASE}/tickets/{tid}/spam", auth=AUTH, timeout=30)
    ok = r.status_code in (200, 204)
    print(f"             ...mark-as-spam HTTP {r.status_code}"
          f"{'' if ok else ' - not available here, mark it in the UI instead'}")

# --- E24 CANARY - real mailbox, the inbox oracle for Limitation #5
if CANARY and contacts.get("canary"):
    for i in range(1, 6):
        t = mkticket(f"E24-{i}",
                     f"[E24-{i}] CANARY - watch {CANARY} during the run",
                     requester_id=contacts["canary"],
                     status=[2, 3, 4, 5, 2][i - 1],
                     description=f"<p>Canary ticket {i}. If this mailbox receives "
                                 "anything during the migration run, the "
                                 "zero-email guarantee has failed.</p>")
        if t and i <= 2:
            conv(t, f"E24-{i}", "<p>Customer follow-up on the canary ticket.</p>",
                 incoming=True, user=contacts["canary"])
            conv(t, f"E24-{i}", "<p>Internal note - not customer visible.</p>",
                 private=True)
else:
    print("  ! E24 canary skipped (set FD_CANARY_EMAIL to enable)")

MANIFEST.write_text(json.dumps(made, indent=2), encoding="utf-8")
print(f"\nmanifest -> {MANIFEST}")
print(f"tickets created: {len(made['tickets'])}   tag: {TAG}")
if _fail:
    print(f"\n{len(_fail)} call(s) did not succeed:")
    for f in _fail:
        print(f"  - {f}")
    print("Anything unsupported on a trial plan is expected - record it and move on.")
