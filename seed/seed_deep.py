"""Seed the SOURCE account with the DEEP fidelity dataset.

This is the dataset that decides whether a migration tool is actually good.
`seed_demo_10.py` looks realistic; this one is built to break things.

What it covers that nothing else does:
  - CROSS-AGENT work: a ticket opened by one agent, handed to another mid-thread,
    with the group changing too - so responder history, not just final state
  - EMAIL SIGNATURES and growing QUOTED-REPLY CHAINS, the way real mail threads
    actually bloat, including a legal disclaimer footer
  - ATTACHMENTS IN EVERY POSITION: description, customer reply, agent reply,
    private note, two on one message, the same filename twice, a unicode
    filename, and a 1.5 MB file
  - an INLINE data: URI image inside an HTML body
  - a FORWARDED email with headers preserved in the body
  - a 40-MESSAGE thread, past every default page size
  - a full STATUS JOURNEY with a message at each step
  - time logged by TWO different agents on one ticket

Usage:
    export FD_SOURCE_DOMAIN=https://yourtrial.freshdesk.com
    export FD_SOURCE_API_KEY=...
    python -X utf8 seed/seed_deep.py

Conversations are notes (customer = incoming, agent = public), so seeding never
emails anyone. Everything is tagged  zzz-deep  for cleanup. Re-running resumes
from the manifest instead of creating duplicates.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

import _fixtures as fx

_DOMAIN = os.environ.get("FD_SOURCE_DOMAIN", "").strip().rstrip("/")
if not _DOMAIN:
    raise SystemExit(
        "Set FD_SOURCE_DOMAIN to the account you want to SEED, e.g.\n"
        "  export FD_SOURCE_DOMAIN=https://yourtrial.freshdesk.com\n"
        "  export FD_SOURCE_API_KEY=...\n"
        "Point this at a TEST account only - it writes ~16 tickets.")
if not _DOMAIN.startswith("http"):
    _DOMAIN = "https://" + _DOMAIN
BASE = f"{_DOMAIN}/api/v2"
try:
    AUTH = (os.environ["FD_SOURCE_API_KEY"], "X")
except KeyError:
    raise SystemExit("Set FD_SOURCE_API_KEY (admin API key for the source account).")

TAG = "zzz-deep"
FILES = Path(__file__).parent / "_deep_files"
MANIFEST = Path(__file__).parent / "_deep_manifest.json"
THROTTLE = 1.3

made: dict = {"domain": _DOMAIN, "tickets": {}}
_fail: list = []
_existing: dict = {}
if MANIFEST.exists():
    try:
        prev = json.loads(MANIFEST.read_text(encoding="utf-8"))
        if prev.get("domain") == _DOMAIN:
            _existing = prev.get("tickets", {})
            if _existing:
                print(f"resuming: {len(_existing)} ticket(s) already exist")
    except Exception:                                          # noqa: BLE001
        pass


def _req(method, path, label, **kw):
    for _ in range(4):
        try:
            r = requests.request(method, BASE + path, auth=AUTH, timeout=120, **kw)
        except Exception as exc:                               # noqa: BLE001
            print(f"    x {label}: {exc}")
            _fail.append(label)
            return None
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", 30)) + 1
            print(f"    . rate limited, sleeping {wait}s")
            time.sleep(wait)
            continue
        time.sleep(THROTTLE)
        if r.status_code in (200, 201, 204):
            return r.json() if r.text.strip() else {}
        print(f"    x {label}: HTTP {r.status_code} {r.text[:170]}")
        _fail.append(f"{label} ({r.status_code})")
        return None
    _fail.append(f"{label} (rate limit)")
    return None


def get(path, **p):
    r = requests.get(BASE + path, auth=AUTH, params={"per_page": 100, **p}, timeout=60)
    time.sleep(0.35)
    return r.json() if r.ok else []


def send(path, label, fields, files=None):
    """Freshdesk takes JSON or multipart, never form-urlencoded - requests
    silently falls back to urlencoded when `files` is empty, which 415s."""
    if not files:
        return _req("POST", path, label,
                    json={k: v for k, v in fields.items() if v is not None})
    data = []
    for k, v in fields.items():
        if v is None:
            continue
        if isinstance(v, list):
            data += [(f"{k}[]", str(i)) for i in v]
        elif isinstance(v, dict):
            data += [(f"{k}[{ck}]", str(cv)) for ck, cv in v.items()]
        elif isinstance(v, bool):
            data.append((k, "true" if v else "false"))
        else:
            data.append((k, str(v)))
    handles = [(FILES / n).open("rb") for n in files]
    try:
        return _req("POST", path, label, data=data,
                    files=[("attachments[]", (n, h, fx.MIME[Path(n).suffix]))
                           for n, h in zip(files, handles)])
    finally:
        for h in handles:
            h.close()


# ---------------------------------------------------------------- fixtures
print(f"seeding DEEP dataset into {_DOMAIN}\n")
sizes = fx.build(FILES)
print("fixtures:")
for n, s in sizes.items():
    print(f"  {n:<32} {s:>9,} B")
INLINE = fx.png_data_uri()

agents = get("/agents")
if len(agents) < 1:
    raise SystemExit("no agents on the source account")
A = agents[0]["id"]
B = agents[1]["id"] if len(agents) > 1 else A
AN = (agents[0].get("contact") or {}).get("name", "Agent A")
BN = ((agents[1].get("contact") or {}).get("name", "Agent B")
      if len(agents) > 1 else AN)
print(f"\nagents: A={A} ({AN})   B={B} ({BN})")
if A == B:
    print("  ! only one agent - cross-agent cases will be weaker")

groups = {g["name"]: g["id"] for g in get("/groups")}
print(f"groups: {list(groups) or 'NONE - run seed_demo_10.py first'}")

contacts = {}
for c in get("/contacts"):
    if c.get("email"):
        contacts[c["email"]] = c["id"]
CUST = next((v for k, v in contacts.items() if "sarah.obrien" in k), None)
CUST2 = next((v for k, v in contacts.items() if "james.whitfield" in k), None)
if not CUST:
    CUST = list(contacts.values())[0] if contacts else None
if not CUST2:
    CUST2 = CUST
if not CUST:
    raise SystemExit("no contacts on the source account - run seed_demo_10.py first")

# ------------------------------------------------------------- signatures
SIG_A = (f"<p>--<br><b>{AN}</b><br>Technical Support Engineer<br>"
         "SDK Support &middot; +353 1 555 0800<br>"
         "<a href=\"https://example.com/support\">example.com/support</a></p>")
SIG_B = (f"<p>--<br><b>{BN}</b><br>Senior Support Specialist &middot; Escalations"
         "<br>SDK Support &middot; +353 1 555 0844</p>")
SIG_CUST = ("<p>--<br>Sarah O'Brien<br>Retail Operations Manager<br>"
            "Northgate Retail &middot; +353 1 555 0110</p>")
DISCLAIMER = (
    "<p style=\"font-size:10px;color:#888\">This e-mail and any attachments are "
    "confidential and intended solely for the addressee. If you have received "
    "this message in error, please notify the sender and delete it. Northgate "
    "Retail Ltd, registered in Ireland No. 411902. Registered office: 4 Harbour "
    "Court, Dublin 2.</p>")


def quoted(prev: str, author: str, when: str) -> str:
    """Wrap the previous message the way a mail client would."""
    return (f"<blockquote style=\"border-left:2px solid #ccc;padding-left:10px;"
            f"color:#555\"><p>On {when}, {author} wrote:</p>{prev}</blockquote>")


# ---------------------------------------------------------------- helpers
def ticket(case, subject, *, body, requester=None, status=2, priority=2,
           ttype="Question", source=1, group=None, responder=None, tags=None,
           cc=None, ref=None, files=None):
    if case in _existing:
        tid = _existing[case]["id"]
        made["tickets"][case] = _existing[case]
        print(f"  = {case:<5} -> #{tid:<5} {subject[:52]}  (exists)")
        return tid
    f = {"subject": subject, "description": body, "status": status,
         "priority": priority, "type": ttype, "source": source,
         "requester_id": requester or CUST,
         "responder_id": responder if responder is not None else A,
         "tags": [TAG, case.lower()] + (tags or [])}
    if group and group in groups:
        f["group_id"] = groups[group]
    if cc:
        f["cc_emails"] = cc
    if ref:
        f["custom_fields"] = {"cf_reference_number": ref}
    t = send("/tickets", case, f, files)
    if not t:
        return None
    made["tickets"][case] = {"id": t["id"], "subject": subject}
    print(f"  + {case:<5} -> #{t['id']:<5} {subject[:52]}")
    return t["id"]


def cust(tid, body, who=None, files=None):
    """Customer message - incoming note is the only API route to one."""
    return send(f"/tickets/{tid}/notes", "   cust",
                {"body": body, "private": False, "incoming": True,
                 "user_id": who or CUST}, files)


_downgraded = []


def agent(tid, body, who=None, private=False, files=None):
    """Post an agent message.

    The public v2 API will not attribute a note to any agent other than the
    API key's owner - it returns 403 invalid_user even for an Account
    Administrator with Global Access. So when `who` is somebody else we try,
    and fall back to the key owner, recording that we had to. That downgrade
    is the single most important thing this dataset demonstrates: a migration
    driven by one API key CANNOT preserve per-agent authorship.
    """
    f = {"body": body, "private": private, "user_id": who or A}
    if who and who != A:
        r = _req("POST", f"/tickets/{tid}/notes", "   agent(other)",
                 json=f) if not files else send(
                     f"/tickets/{tid}/notes", "   agent(other)", f, files)
        if r:
            return r
        if _fail and "agent(other)" in _fail[-1]:
            _fail.pop()
        _downgraded.append(tid)
        f["user_id"] = A
        print(f"    . cross-agent attribution refused (403) - posting as key owner")
    return send(f"/tickets/{tid}/notes", "   agent", f, files)


def reassign(tid, *, responder=None, group=None, status=None, priority=None):
    p = {}
    if responder:
        p["responder_id"] = responder
    if group and group in groups:
        p["group_id"] = groups[group]
    if status:
        p["status"] = status
    if priority:
        p["priority"] = priority
    return _req("PUT", f"/tickets/{tid}", "   reassign", json=p)


def hours(tid, spent, who, note):
    return _req("POST", f"/tickets/{tid}/time_entries", "   time",
                json={"time_spent": spent, "agent_id": who, "billable": True,
                      "note": note})


print("\n== tickets ==")

# ---- D01  cross-agent handoff, signatures, growing quoted chain ----------
m1 = ("<div><p>Hi Support,</p><p>Since last night none of our store managers "
      "can sign in - they get <i>SAML assertion could not be validated</i>. "
      "This is blocking 40 people.</p>" + SIG_CUST + DISCLAIMER + "</div>")
t = ticket("D01", "Cross-agent handoff: SSO failure blocking 40 users",
           body=m1, status=2, priority=4, ttype="Incident", source=1,
           group="Tier 1 Support", responder=A, ref=200101,
           tags=["sso", "cross-agent"], files=["screenshot-error.png"])
if t:
    m2 = ("<div><p>Hi Sarah,</p><p>Thanks for the detail. I can see failed "
          "assertions from 23:40. Did your team rotate the signing "
          "certificate?</p>" + SIG_A
          + quoted(m1, "Sarah O'Brien", "Thu, 27 Aug at 09:12") + "</div>")
    agent(t, m2)
    m3 = ("<div><p>Yes - rotated Monday evening. New metadata attached.</p>"
          + SIG_CUST + quoted(m2, AN, "Thu, 27 Aug at 09:40") + DISCLAIMER
          + "</div>")
    cust(t, m3, files=["repro-notes.docx"])
    agent(t, f"<p>Internal: stale cached IdP metadata on the EU pod. Beyond "
             f"Tier 1 - handing to {BN} in Escalations. Customer is P1.</p>",
          private=True)
    reassign(t, responder=B, group="Escalations", priority=4)
    m4 = ("<div><p>Hi Sarah, " + BN + " here - I have taken this over from "
          + AN + ". Metadata cache refreshed on our side; please retry.</p>"
          + SIG_B + quoted(m3, "Sarah O'Brien", "Thu, 27 Aug at 10:05")
          + "</div>")
    agent(t, m4, who=B)
    cust(t, "<div><p>Confirmed - all managers can sign in. Thank you both.</p>"
            + SIG_CUST + "</div>")
    agent(t, "<p>Internal: root cause = metadata TTL not honoured. Follow-up "
             "PLAT-3402 raised.</p>", who=B, private=True)
    reassign(t, status=4)

# ---- D02  internal consult between two agents ---------------------------
t = ticket("D02", "Internal consult: refund above approval threshold",
           body="<div><p>We were charged twice for August and need the "
                "duplicate refunded.</p>" + SIG_CUST + "</div>",
           status=3, priority=3, ttype="Problem", source=2,
           group="Billing & Accounts", responder=A, ref=200102,
           tags=["billing", "cross-agent"])
if t:
    agent(t, f"<p>Internal: refund is EUR 3,120 - over my limit. {BN}, can you "
             "approve?</p>", private=True)
    agent(t, "<p>Internal: approved. Reference APP-8841. Process as a credit "
             "note, not a card refund.</p>", who=B, private=True)
    agent(t, "<div><p>Hi Sarah,</p><p>Approved - credit note CN-2026-0233 has "
             "been raised. It will appear on your next statement.</p>"
             + SIG_A + "</div>")
    cust(t, "<div><p>Perfect, thanks.</p>" + SIG_CUST + "</div>")

# ---- D03  attachments in every position ---------------------------------
t = ticket("D03", "Attachments in every position (desc/reply/note, dup name)",
           body="<div><p>Two files attached to this first message.</p></div>",
           status=2, priority=2, ttype="Incident", source=1,
           group="Tier 1 Support", tags=["attachments"],
           files=["screenshot-error.png", "invoice-INV-2026-0912.pdf"])
if t:
    cust(t, "<p>Adding the console output.</p>", files=["screenshot-console.png"])
    agent(t, "<p>Thanks - here is the log we pulled.</p>", files=["api-error.log"])
    agent(t, "<p>Internal: diagnostics bundle from the customer's host.</p>",
          private=True, files=["export-sample.csv"])
    # same filename again, on a different message - tests name collision
    agent(t, "<p>Re-sending the same file name from a different message.</p>",
          files=["screenshot-error.png"])

# ---- D04  unicode filename + 1.5 MB file --------------------------------
t = ticket("D04", "Unicode filename and a 1.5 MB attachment",
           body="<div><p>Quarterly report attached; the FILE NAME is the test "
                "here.</p></div>",
           status=2, priority=2, ttype="Question", source=2,
           group="Tier 1 Support", tags=["attachments", "unicode"],
           files=["отчёт-2026-Q3_报告.pdf"])
if t:
    agent(t, "<p>Diagnostics bundle attached (1.5 MB).</p>",
          files=["diagnostics-bundle.zip"])

# ---- D05  inline data: URI image ----------------------------------------
t = ticket("D05", "Inline image embedded in the HTML body",
           body=("<div><p>The error looks like this:</p>"
                 f"<p><img src=\"{INLINE}\" alt=\"inline error\" "
                 "width=\"240\" height=\"120\"></p>"
                 "<p>It appears on every page load.</p></div>"),
           status=2, priority=2, ttype="Incident", source=1,
           group="Tier 1 Support", tags=["inline-image"])
if t:
    agent(t, "<div><p>Received - the inline image came through on our side "
             f"as:</p><p><img src=\"{INLINE}\" width=\"240\"></p>" + SIG_A
             + "</div>")

# ---- D06  forwarded email with headers ----------------------------------
t = ticket("D06", "Fwd: Payment failed for order A-4471",
           body=("<div><p>Forwarding this from our finance mailbox.</p><hr>"
                 "<p>---------- Forwarded message ----------<br>"
                 "<b>From:</b> billing-noreply@payments.example.com<br>"
                 "<b>Sent:</b> Thursday, 27 August 2026 08:14<br>"
                 "<b>To:</b> ap.team@northgate.example.com<br>"
                 "<b>Subject:</b> Payment failed for order A-4471</p>"
                 "<p>Your payment of EUR 312.00 could not be processed. "
                 "Reason: <b>do_not_honour</b>.</p></div>"),
           status=3, priority=3, ttype="Problem", source=1,
           group="Billing & Accounts", tags=["forwarded"])
if t:
    agent(t, "<div><p>Thanks for forwarding. do_not_honour comes from the "
             "issuing bank, not us - the customer will need to contact "
             "them.</p>" + SIG_A + "</div>")

# ---- D07  CC list that grows --------------------------------------------
# NOTE: cc_emails is create-only. PUT /tickets/{id} rejects it with
# 400 invalid_field, so a CC list cannot be modified after the fact - which
# also means a migration must carry the full list on the create call.
t = ticket("D07", "Multi-address CC list (create-time only)",
           body="<div><p>Three colleagues copied from the outset.</p></div>",
           status=2, priority=2, ttype="Question", source=1,
           group="Tier 1 Support", tags=["cc"],
           cc=["watcher1@northgate.example.com",
               "watcher2@northgate.example.com",
               "hr@northgate.example.com"])
if t:
    agent(t, "<p>All three are copied on this thread.</p>")
    cust(t, "<p>Thanks, please keep them on it.</p>")

# ---- D08  description only, zero conversations --------------------------
ticket("D08", "Description only - zero conversations",
       body="<div><p>A ticket that was never replied to. The conversation "
            "array is empty, which is its own edge case.</p>"
            + SIG_CUST + "</div>",
       status=2, priority=1, ttype="Question", source=2,
       group="Tier 1 Support", tags=["empty-thread"])

# ---- D09  full status journey -------------------------------------------
t = ticket("D09", "Full status journey: Open to Closed with a step each",
           body="<div><p>Tracking a ticket through every status.</p></div>",
           status=2, priority=2, ttype="Incident", source=1,
           group="Tier 1 Support", tags=["status-journey"], ref=200109)
if t:
    agent(t, "<p>Acknowledged, investigating.</p>")
    reassign(t, status=3)
    agent(t, "<p>Need more detail from you before we can continue.</p>")
    reassign(t, status=6)
    cust(t, "<p>Detail supplied.</p>")
    reassign(t, status=7)
    agent(t, "<p>Waiting on the vendor now.</p>", who=B)
    reassign(t, status=4)
    cust(t, "<p>Confirmed fixed, happy to close.</p>")
    reassign(t, status=5)

# ---- D10  time logged by two agents -------------------------------------
t = ticket("D10", "Time logged by two different agents",
           body="<div><p>Long-running investigation billed across two "
                "engineers.</p></div>",
           status=4, priority=3, ttype="Problem", source=1,
           group="Escalations", tags=["time-tracking"], ref=200110)
if t:
    agent(t, "<p>Initial triage complete.</p>")
    hours(t, "01:15", A, "Triage and log analysis")
    agent(t, "<p>Escalated analysis complete.</p>", who=B)
    hours(t, "02:30", B, "Deep-dive and patch verification")
    hours(t, "00:20", A, "Customer update call")

# ---- D11  every custom field populated ----------------------------------
cf = {}
for f in get("/ticket_fields"):
    if f.get("default"):
        continue
    n, ty = f.get("name"), f.get("type")
    if ty == "custom_number":
        cf[n] = 200111
    elif ty in ("custom_text", "custom_paragraph"):
        cf[n] = "R-C probe - unicode and <b>markup</b>"
    elif ty == "custom_checkbox":
        cf[n] = True
    elif ty == "custom_decimal":
        cf[n] = 42.75
    elif ty == "custom_dropdown" and f.get("choices"):
        ch = f["choices"]
        cf[n] = ch[0] if isinstance(ch[0], str) else list(ch)[0]
t = ticket("D11", "Every custom field carries a value (risk R-C)",
           body="<div><p>Custom-field VALUE migration is the one residual risk "
                "with code coverage but no data behind it.</p></div>",
           status=2, priority=2, ttype="Question", source=2,
           group="Tier 1 Support", tags=["custom-fields"])
if t and cf:
    _req("PUT", f"/tickets/{t}", "   set cf", json={"custom_fields": cf})
    print(f"        custom_fields = {json.dumps(cf, ensure_ascii=False)[:110]}")

# ---- D12 / D13  cross-referenced pair -----------------------------------
a = ticket("D12", "Original report: checkout timing out",
           body="<div><p>Checkout times out at the payment step.</p></div>",
           status=2, priority=3, ttype="Incident", source=1,
           group="Tier 1 Support", tags=["duplicate-pair"])
b = ticket("D13", "Duplicate report: checkout timing out (second customer)",
           body="<div><p>Same symptom reported separately.</p></div>",
           requester=CUST2, status=2, priority=3, ttype="Incident", source=2,
           group="Tier 1 Support", tags=["duplicate-pair"])
if a and b:
    agent(a, f"<p>Internal: duplicate of this is #{b}. Keeping both open - "
             "Freshdesk merge is not exposed on the v2 API, so a migration "
             "cannot reproduce a merge either.</p>", private=True)
    agent(b, f"<p>Internal: duplicate of #{a}.</p>", private=True)

# ---- D14  agent as the requester ----------------------------------------
ae = (agents[0].get("contact") or {}).get("email")
if ae:
    t = ticket("D14", "Agent raising a ticket as the requester",
               body="<div><p>Raised internally by a member of staff, so the "
                    "requester is also an agent.</p></div>",
               status=2, priority=2, ttype="Question", source=2,
               group="Tier 1 Support", tags=["identity-edge"])
    if t:
        _req("PUT", f"/tickets/{t}", "   set requester",
             json={"email": ae})
        agent(t, "<p>Picked up.</p>", who=B)

# ---- D15  40-message thread ---------------------------------------------
t = ticket("D15", "Forty-message thread - past every default page size",
           body="<div><p>A genuinely long support thread.</p></div>",
           status=3, priority=2, ttype="Problem", source=1,
           group="Tier 1 Support", tags=["long-thread"], ref=200115)
if t:
    for i in range(1, 41):
        if i % 2 == 1:
            cust(t, f"<div><p>Customer message {i} of 40. Still seeing the "
                    f"issue after step {i // 2 + 1}.</p>" + SIG_CUST + "</div>")
        elif i % 8 == 0:
            agent(t, f"<p>Internal checkpoint at message {i}.</p>",
                  who=B if i % 16 == 0 else A, private=True)
        else:
            agent(t, f"<div><p>Agent message {i} of 40. Please try the next "
                     f"step.</p>" + SIG_A + "</div>")
        if i % 10 == 0:
            print(f"        ...{i}/40 messages")

MANIFEST.write_text(json.dumps(made, indent=2, ensure_ascii=False), encoding="utf-8")
ids = [v["id"] for v in made["tickets"].values()]
print(f"\nmanifest -> {MANIFEST}")
print(f"created/reused {len(ids)} tickets, tag '{TAG}'")
print("ids: " + ",".join(str(i) for i in sorted(ids)))
if _fail:
    print(f"\n{len(_fail)} call(s) did not succeed:")
    for f in _fail[:25]:
        print(f"  - {f}")
