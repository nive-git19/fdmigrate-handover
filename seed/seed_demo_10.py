"""Seed the SOURCE account with 10 realistic tickets for a Help Desk Migration
(HDM) demo migration.

Built to look like a real support desk, while quietly covering every field HDM
puts on its ticket-mapping screen - so the wizard has something to map and the
demo result table means something:

  statuses   2,3,4,5,6,7  (incl. Waiting on Customer / Waiting on Third Party)
  priorities 1,2,3,4      types  Incident / Problem / Question / Feature Request
  sources    Email / Portal / Phone
  groups     3 (the source account has none today - HDM's Group mapping is a
             dead dropdown without them)
  custom     cf_reference_number carries VALUES (HDM shows it as "Reference
             Number"; empty fields demo as empty)
  plus       attachments (PNG/PDF/CSV/TXT/DOCX), CC lists, private notes,
             time logs, unicode subjects and bodies, a phone-only contact

Attachment fixtures are generated here - real, valid files, no binaries in git.

Usage:
    export FD_SOURCE_DOMAIN=https://yourtrial.freshdesk.com
    export FD_SOURCE_API_KEY=...
    python -X utf8 seed/seed_demo_10.py

Conversations are written as NOTES (customer = incoming, agent = public), not
as /reply posts, so seeding cannot email anyone. HDM migrates both as
"Comments". Pass --replies to use real /reply posts instead - only do that with
the account's outbound email switched OFF.

Everything is tagged  zzz-demo-10  for cleanup.
"""
from __future__ import annotations

import io
import json
import os
import struct
import sys
import time
import zipfile
import zlib
from pathlib import Path

import requests

_DOMAIN = os.environ.get("FD_SOURCE_DOMAIN", "").strip().rstrip("/")
if not _DOMAIN:
    raise SystemExit(
        "Set FD_SOURCE_DOMAIN to the account you want to SEED, e.g.\n"
        "  export FD_SOURCE_DOMAIN=https://yourtrial.freshdesk.com\n"
        "  export FD_SOURCE_API_KEY=...\n"
        "Point this at a TEST account only - it writes 10 tickets.")
if not _DOMAIN.startswith("http"):
    _DOMAIN = "https://" + _DOMAIN
BASE = f"{_DOMAIN}/api/v2"
try:
    AUTH = (os.environ["FD_SOURCE_API_KEY"], "X")
except KeyError:
    raise SystemExit("Set FD_SOURCE_API_KEY (admin API key for the source account).")

USE_REPLIES = "--replies" in sys.argv
TAG = "zzz-demo-10"
FILES = Path(__file__).parent / "_demo_files"
MANIFEST = Path(__file__).parent / "_demo_manifest.json"
THROTTLE = 1.3          # trials are capped at 50 calls/min

made: dict = {"domain": _DOMAIN, "tickets": [], "groups": {}, "contacts": {},
              "companies": {}}
_fail: list = []

# Resume: a previous run may have created some tickets. Reuse their ids rather
# than creating a second copy.
_existing: dict = {}
if MANIFEST.exists():
    try:
        prev = json.loads(MANIFEST.read_text(encoding="utf-8"))
        if prev.get("domain") == _DOMAIN:
            _existing = {t["n"]: t for t in prev.get("tickets", [])}
            if _existing:
                print(f"resuming: {len(_existing)} ticket(s) already exist "
                      f"({', '.join('T%s=#%s' % (k, v['id']) for k, v in sorted(_existing.items()))})")
    except Exception:                                          # noqa: BLE001
        pass


# --------------------------------------------------------------------- http
def _req(method, path, label, **kw):
    for attempt in range(4):
        try:
            r = requests.request(method, BASE + path, auth=AUTH, timeout=90, **kw)
        except Exception as exc:                              # noqa: BLE001
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


def post(path, label, **kw):
    return _req("POST", path, label, **kw)


def get(path, **params):
    r = requests.get(BASE + path, auth=AUTH, params=params, timeout=60)
    time.sleep(0.4)
    return r.json() if r.ok else []


def send(path: str, label: str, fields: dict, files: list | None = None):
    """Freshdesk accepts JSON *or* multipart, never form-urlencoded. requests
    falls back to urlencoded when `files` is empty, which 415s - so pick the
    encoding explicitly based on whether there is actually a file."""
    if not files:
        return post(path, label, json={k: v for k, v in fields.items()
                                       if v is not None})
    f = form(fields, files)
    r = post(path, label, data=f["data"], files=f["files"])
    for _, fh in f["files"]:
        fh[1].close()
    return r


def form(fields: dict, files: list | None = None):
    """Freshdesk multipart: arrays repeat the key with [], files are attachments[]."""
    data = []
    for k, v in fields.items():
        if v is None:
            continue
        if isinstance(v, list):
            for item in v:
                data.append((f"{k}[]", str(item)))
        elif isinstance(v, dict):
            for ck, cv in v.items():
                data.append((f"{k}[{ck}]", str(cv)))
        elif isinstance(v, bool):
            data.append((k, "true" if v else "false"))
        else:
            data.append((k, str(v)))
    return {"data": data, "files": files or []}


# ------------------------------------------------------------- attachments
def _png(w, h, path):
    """Valid RGB PNG - a plausible 'screenshot', built with stdlib only."""
    rows = b""
    for y in range(h):
        row = bytearray()
        for x in range(w):
            band = y < 34
            if band:
                row += bytes((36, 42, 58))
            elif 40 < y < 92 and 20 < x < w - 20:
                row += bytes((220, 76, 70)) if y < 66 else bytes((246, 246, 248))
            else:
                g = 248 - (y * 40 // max(h, 1))
                row += bytes((g, g, min(255, g + 4)))
        rows += b"\x00" + bytes(row)

    def chunk(tag, data):
        body = tag + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(rows, 9))
           + chunk(b"IEND", b""))
    path.write_bytes(png)


def _pdf(title, lines, path):
    """Minimal but valid PDF 1.4, one page, Helvetica."""
    def esc(s):
        return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    content = f"BT /F1 15 Tf 56 780 Td ({esc(title)}) Tj ET\n"
    y = 752
    for ln in lines:
        content += f"BT /F1 10 Tf 56 {y} Td ({esc(ln)}) Tj ET\n"
        y -= 15
    cb = content.encode("latin-1", "replace")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(cb)).encode() + b" >>\nstream\n" + cb + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 " + str(len(objs) + 1).encode() + b"\n0000000000 65535 f \n"
    for off in offsets:
        out += ("%010d 00000 n \n" % off).encode()
    out += (b"trailer\n<< /Size " + str(len(objs) + 1).encode()
            + b" /Root 1 0 R >>\nstartxref\n" + str(xref).encode() + b"\n%%EOF\n")
    path.write_bytes(out)


def _docx(title, paras, path):
    """Minimal valid .docx (Office Open XML)."""
    def p(t):
        return ("<w:p><w:r><w:t xml:space=\"preserve\">"
                + t.replace("&", "&amp;").replace("<", "&lt;")
                + "</w:t></w:r></w:p>")

    doc = ("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
           "<w:document xmlns:w=\"http://schemas.openxmlformats.org/"
           "wordprocessingml/2006/main\"><w:body>"
           + p(title) + "".join(p(x) for x in paras)
           + "<w:sectPr><w:pgSz w:w=\"11906\" w:h=\"16838\"/></w:sectPr>"
             "</w:body></w:document>")
    ct = ("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
          "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/"
          "content-types\">"
          "<Default Extension=\"rels\" ContentType=\"application/"
          "vnd.openxmlformats-package.relationships+xml\"/>"
          "<Default Extension=\"xml\" ContentType=\"application/xml\"/>"
          "<Override PartName=\"/word/document.xml\" ContentType=\"application/"
          "vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml\"/>"
          "</Types>")
    rels = ("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
            "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/"
            "2006/relationships\"><Relationship Id=\"rId1\" Type=\"http://"
            "schemas.openxmlformats.org/officeDocument/2006/relationships/"
            "officeDocument\" Target=\"word/document.xml\"/></Relationships>")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc)


def build_fixtures():
    FILES.mkdir(exist_ok=True)
    _png(720, 420, FILES / "sso-error-screenshot.png")
    _pdf("INVOICE INV-2026-0871",
         ["Vertex Health Ltd", "Billing period: 01-31 July 2026", "",
          "Line 1  Platform subscription (25 agents)      EUR 1,875.00",
          "Line 2  Platform subscription (25 agents)      EUR 1,875.00   <-- duplicate",
          "", "Total charged                                EUR 3,750.00",
          "Expected                                     EUR 1,875.00"],
         FILES / "invoice-INV-2026-0871.pdf")
    (FILES / "account-export-sample.csv").write_text(
        "record_id,type,created_at,subject,requester_email,status\n"
        "44100,ticket,2026-05-02,Login issue,michael.chen@lumen-labs.example.com,closed\n"
        "44118,ticket,2026-05-14,Billing query,michael.chen@lumen-labs.example.com,closed\n"
        "44190,ticket,2026-06-01,API access,michael.chen@lumen-labs.example.com,open\n",
        encoding="utf-8")
    (FILES / "api-error-log.txt").write_text(
        "\n".join(
            f"2026-08-2{d} 14:0{d}:11 ERROR upstream=orders-svc status=502 "
            f"latency_ms={780 + d * 37} trace={'a1f' + str(d) * 4}"
            for d in range(1, 9))
        + "\n2026-08-28 14:09:02 WARN  connection pool exhausted (max=32)\n",
        encoding="utf-8")
    _docx("Password reset - reproduction notes",
          ["1. Request a reset link from the portal.",
           "2. Open the link within 30 seconds.",
           "3. Observe: 'This link has expired.'",
           "Environment: Chrome 141, Windows 11, EU pod.",
           "Workaround: request the link twice; the second one works."],
          FILES / "reset-flow-notes.docx")
    print(f"fixtures -> {FILES}")
    for f in sorted(FILES.iterdir()):
        print(f"  {f.name:<32} {f.stat().st_size:>7,} B")


MIME = {".png": "image/png", ".pdf": "application/pdf", ".csv": "text/csv",
        ".txt": "text/plain",
        ".docx": "application/vnd.openxmlformats-officedocument."
                 "wordprocessingml.document"}


def attach(name):
    p = FILES / name
    return ("attachments[]", (name, p.open("rb"), MIME[p.suffix]))


# ------------------------------------------------------------------- setup
print(f"seeding demo dataset into {_DOMAIN}\n")
build_fixtures()

agents = get("/agents", per_page=100)
if not agents:
    raise SystemExit("no agents on the source account - create one first")
A1 = agents[0]["id"]
A2 = agents[1]["id"] if len(agents) > 1 else A1
print(f"\nagents: {A1}, {A2}")

print("\n== groups ==")
existing_groups = {g["name"]: g["id"] for g in get("/groups", per_page=100)}
GROUPS = {}
for name, desc in [("Tier 1 Support", "First-line support queue"),
                   ("Billing & Accounts", "Invoices, refunds, subscriptions"),
                   ("Escalations", "P1 incidents and exec escalations")]:
    if name in existing_groups:
        GROUPS[name] = existing_groups[name]
        print(f"  = {name} -> {GROUPS[name]}")
        continue
    g = post("/groups", f"group {name}", json={"name": name, "description": desc})
    if g:
        GROUPS[name] = g["id"]
        print(f"  + {name} -> {g['id']}")
made["groups"] = GROUPS

print("\n== companies ==")
existing_co = {c["name"]: c["id"] for c in get("/companies", per_page=100)}
COMPANIES = {}
for name, dom in [("Northgate Retail", "northgate.example.com"),
                  ("Lumen Labs", "lumen-labs.example.com"),
                  ("Vertex Health", "vertex-health.example.com")]:
    if name in existing_co:
        COMPANIES[name] = existing_co[name]
        print(f"  = {name} -> {COMPANIES[name]}")
        continue
    c = post("/companies", f"company {name}",
             json={"name": name, "domains": [dom],
                   "description": f"{name} - demo account"})
    if c:
        COMPANIES[name] = c["id"]
        print(f"  + {name} -> {c['id']}")
made["companies"] = COMPANIES

print("\n== contacts ==")
PEOPLE = [
    ("sarah",  {"name": "Sarah O'Brien", "email": "sarah.obrien@northgate.example.com",
                "job_title": "Retail Operations Manager", "phone": "+353 1 555 0110",
                "company": "Northgate Retail"}),
    ("priya",  {"name": "Priya Raghavan",
                "email": "priya.raghavan@vertex-health.example.com",
                "job_title": "Finance Lead", "company": "Vertex Health"}),
    ("michael", {"name": "Michael Chen", "email": "michael.chen@lumen-labs.example.com",
                 "job_title": "Data Protection Officer", "company": "Lumen Labs"}),
    ("daniel", {"name": "Daniel Okafor", "email": "daniel.okafor@lumen-labs.example.com",
                "job_title": "Product Owner", "company": "Lumen Labs"}),
    ("james",  {"name": "James Whitfield",
                "email": "james.whitfield@northgate.example.com",
                "job_title": "Platform Engineer", "company": "Northgate Retail"}),
    ("tomas",  {"name": "Tomás Ó Súilleabháin",
                "email": "tomas.osuilleabhain@claddagh.example.com",
                "job_title": "IT Administrator"}),
    ("meiling", {"name": "陈美玲 (Chen Meiling)",
                 "email": "meiling.chen@hanwei.example.com",
                 "job_title": "Logistics Coordinator"}),
    ("aoife",  {"name": "Aoife Byrne", "phone": "+353 1 555 0147",
                "job_title": "Store Supervisor"}),      # phone only, no email
]
CONTACTS = {}
for key, payload in PEOPLE:
    co = payload.pop("company", None)
    if co and co in COMPANIES:
        payload["company_id"] = COMPANIES[co]
    c = post("/contacts", f"contact {key}", json=payload)
    if not c and payload.get("email"):
        found = get("/contacts", email=payload["email"])
        c = found[0] if found else None
        if c:
            print(f"  = {key} exists -> {c['id']}")
    if c:
        CONTACTS[key] = c["id"]
        if c.get("id"):
            print(f"  + {key:<8} -> {c['id']}  {payload.get('name')}")
made["contacts"] = CONTACTS

# ----------------------------------------------------------------- tickets
print("\n== tickets ==")


def ticket(num, subject, *, requester, status, priority, ttype, source, group,
           responder, tags, ref=None, cc=None, files=None, body):
    fields = {
        "subject": subject, "description": body, "status": status,
        "priority": priority, "type": ttype, "source": source,
        "responder_id": responder, "tags": [TAG] + tags,
    }
    if group:
        fields["group_id"] = GROUPS.get(group)
    if cc:
        fields["cc_emails"] = cc
    if ref:
        fields["custom_fields"] = {"cf_reference_number": ref}
    rid = CONTACTS.get(requester)
    if rid:
        fields["requester_id"] = rid
    else:
        fields["email"] = "demo.fallback@example.com"

    if num in _existing:                       # created by an earlier run
        tid = _existing[num]["id"]
        made["tickets"].append(_existing[num])
        print(f"  = T{num:<2} -> #{tid:<5} {subject[:56]}  (exists, adding notes)")
        return tid

    t = send("/tickets", f"T{num}", fields, [attach(n) for n in (files or [])])
    if not t:
        return None
    made["tickets"].append({"n": num, "id": t["id"], "subject": subject})
    print(f"  + T{num:<2} -> #{t['id']:<5} {subject[:56]}")
    return t["id"]


def msg(tid, body, *, who=None, private=False, files=None):
    """who=None -> agent. Customer messages go in as incoming notes, which is
    the only API route to them; agent messages are public notes unless
    --replies was passed. Neither emails anybody."""
    if who is None and USE_REPLIES and not private:
        fields = {"body": body}
        path = f"/tickets/{tid}/reply"
    else:
        fields = {"body": body, "private": private}
        if who:
            fields["incoming"] = True
            fields["user_id"] = CONTACTS[who]
        else:
            fields["user_id"] = A1
        path = f"/tickets/{tid}/notes"
    return send(path, "  note", fields, [attach(n) for n in (files or [])])


def hours(tid, spent, agent, note, billable=True):
    return post(f"/tickets/{tid}/time_entries", "  time entry",
                json={"time_spent": spent, "agent_id": agent,
                      "billable": billable, "note": note})


SIG = ("<p>--<br>{name}<br>{title}</p>")

# --- T1 -------------------------------------------------------------------
t = ticket(1, "Unable to sign in after the SSO configuration change",
           requester="sarah", status=2, priority=4, ttype="Incident", source=1,
           group="Escalations", responder=A1, ref=100241,
           tags=["sso", "login", "p1"],
           files=["sso-error-screenshot.png"],
           body="<div><p>Hi Support,</p><p>Since your SSO change on Monday "
                "night, <b>none of our 40 store managers can sign in</b>. They "
                "get <i>&quot;SAML assertion could not be validated&quot;</i>. "
                "Screenshot attached.</p><p>This is blocking the shop floor - "
                "can someone look now?</p>"
                "<p>--<br>Sarah O'Brien<br>Retail Operations Manager, "
                "Northgate Retail</p></div>")
if t:
    msg(t, "<p>Hi Sarah,</p><p>Thanks for flagging, and apologies for the "
           "disruption. I can see failed assertions from your IdP starting "
           "23:40 Monday. Could you confirm whether the signing certificate "
           "was rotated on your side?</p><p>Best,<br>Support</p>")
    msg(t, "<p>Yes - our security team rotated it Monday evening. I have "
           "attached the new metadata.</p>", who="sarah")
    msg(t, "<p>Internal: IdP cert rotated, our cached metadata is stale. "
           "Needs a manual refresh on the EU pod. Escalating to platform.</p>",
        private=True)
    msg(t, "<p>Sarah - metadata refreshed on our side. Could you retry and "
           "confirm?</p>")

# --- T2 -------------------------------------------------------------------
t = ticket(2, "Invoice INV-2026-0871 has been charged twice",
           requester="priya", status=3, priority=3, ttype="Problem", source=2,
           group="Billing & Accounts", responder=A2, ref=100242,
           tags=["billing", "refund"],
           cc=["ap.team@vertex-health.example.com",
               "controller@vertex-health.example.com"],
           files=["invoice-INV-2026-0871.pdf"],
           body="<div><p>Hello,</p><p>We have been billed <b>EUR 3,750.00</b> "
                "for July against a contracted <b>EUR 1,875.00</b>. The invoice "
                "shows the subscription line twice - see attached.</p>"
                "<p>Please confirm the refund and the corrected invoice.</p>"
                "<p>--<br>Priya Raghavan<br>Finance Lead, Vertex Health</p></div>")
if t:
    msg(t, "<p>Hi Priya,</p><p>You are right - a duplicate line was created "
           "when the seat count was amended mid-cycle. I have raised credit "
           "note CN-2026-0233 for EUR 1,875.00.</p><p>Refunds settle in 5-7 "
           "working days.</p>")
    msg(t, "<p>Thank you. Could you also confirm the August invoice will show "
           "a single line?</p>", who="priya")
    msg(t, "<p>Internal: credit note raised. Watch the August run - the "
           "amendment job is the likely root cause.</p>", private=True)
    hours(t, "00:45", A2, "Investigated duplicate billing line, raised credit note")

# --- T3 -------------------------------------------------------------------
t = ticket(3, "Request: export of all account data (GDPR Article 15)",
           requester="michael", status=4, priority=2, ttype="Question", source=1,
           group="Tier 1 Support", responder=A1, ref=100243,
           tags=["gdpr", "data-request"],
           body="<div><p>Dear Support,</p><p>Acting as DPO for Lumen Labs, I am "
                "making a subject access request for all data held against our "
                "account, per <b>Article 15 GDPR</b>.</p><p>Machine-readable "
                "format please, within the statutory one month.</p>"
                "<p>--<br>Michael Chen<br>Data Protection Officer</p></div>")
if t:
    msg(t, "<p>Hi Michael,</p><p>Acknowledged. The export is attached as CSV. "
           "It covers tickets, contacts and audit entries for the account.</p>",
        files=["account-export-sample.csv"])
    msg(t, "<p>Received and reviewed - this satisfies the request. Please "
           "close.</p>", who="michael")

# --- T4 -------------------------------------------------------------------
t = ticket(4, "Feature request: bulk CSV upload for the product catalogue",
           requester="daniel", status=2, priority=1, ttype="Feature Request",
           source=2, group="Tier 1 Support", responder=A2,
           tags=["feature-request", "roadmap"],
           body="<div><p>Hi team,</p><p>We add roughly 400 SKUs a month, one "
                "at a time through the UI. A <b>bulk CSV upload</b> would save "
                "us about two days a month.</p><p>Is this on the roadmap?</p>"
                "<p>--<br>Daniel Okafor<br>Product Owner, Lumen Labs</p></div>")
if t:
    msg(t, "<p>Hi Daniel,</p><p>Thanks - logged as PROD-1182 and shared with "
           "the catalogue team. Nothing committed for this quarter, but I will "
           "keep this ticket open and update you when it is scheduled.</p>")

# --- T5 -------------------------------------------------------------------
t = ticket(5, "Intermittent 502 errors on /api/v2/orders since Tuesday",
           requester="james", status=2, priority=4, ttype="Incident", source=1,
           group="Escalations", responder=A1, ref=100245,
           tags=["api", "502", "p1", "platform"],
           files=["api-error-log.txt"],
           body="<div><p>Hi,</p><p>We are seeing <b>502s on roughly 8% of "
                "calls</b> to <code>/api/v2/orders</code> since Tuesday "
                "morning. Latency spikes to ~800ms before the failure. Log "
                "extract attached.</p><p>No change on our side. This is "
                "affecting order capture in 12 stores.</p>"
                "<p>--<br>James Whitfield<br>Platform Engineer</p></div>")
if t:
    msg(t, "<p>Hi James,</p><p>Thanks for the log - the trace ids line up with "
           "a connection-pool exhaustion we are tracking on the orders "
           "service. Raising to engineering now.</p>")
    msg(t, "<p>Internal: pool max=32, sustained concurrency ~48 from this "
           "tenant. Short term bump the pool; long term needs the async "
           "rewrite.</p>", private=True)
    msg(t, "<p>Understood. Any interim mitigation we can apply?</p>",
        who="james")
    msg(t, "<p>We have raised the pool ceiling on the EU pod. Please retry and "
           "let us know the error rate over the next hour.</p>")
    msg(t, "<p>Error rate is down to ~0.4% over the last 40 minutes. Still "
           "watching.</p>", who="james")
    msg(t, "<p>Internal: monitoring for 24h before we close. Root cause ticket "
           "PLAT-3391.</p>", private=True)
    hours(t, "02:15", A1, "Log analysis, pool ceiling change, monitoring")

# --- T6 -------------------------------------------------------------------
t = ticket(6, "配送状況の確認 — order #HW-4471 (retour à l'expéditeur)",
           requester="meiling", status=6, priority=2, ttype="Question", source=1,
           group="Tier 1 Support", responder=A2, ref=100246,
           tags=["shipping", "international"],
           body="<div><p>お世話になっております。</p><p>注文 <b>#HW-4471</b> "
                "の配送状況を確認したいのですが、追跡番号が更新されていません。"
                "</p><p>Le colis a été marqué « retour à l'expéditeur » — "
                "pouvez-vous confirmer ?</p>"
                "<p>--<br>陈美玲 (Chen Meiling)<br>Logistics Coordinator</p></div>")
if t:
    msg(t, "<p>Hello Meiling,</p><p>The carrier shows the parcel returned "
           "because the delivery address was incomplete (no unit number). "
           "Could you confirm the full address so we can re-ship?</p>")
    msg(t, "<p>確認いたしました。Unit 12B を追加してください。ありがとうござい"
           "ます。</p>", who="meiling")

# --- T7 -------------------------------------------------------------------
t = ticket(7, "Follow-up from call: terminal 4 not printing receipts",
           requester="aoife", status=5, priority=2, ttype="Incident", source=3,
           group="Tier 1 Support", responder=A2,
           tags=["hardware", "pos", "phone-call"],
           body="<div><p>Logged on behalf of the customer following a phone "
                "call.</p><p>Terminal 4 at the Blanchardstown store accepts "
                "payment but does not print a receipt. Reported by Aoife "
                "Byrne, store supervisor. No error on screen.</p></div>")
if t:
    msg(t, "<p>Walked the caller through a printer-head reseat and a firmware "
           "check. Receipt printing restored on the second attempt. Advised to "
           "call back if it recurs within 48h.</p>")
    hours(t, "00:30", A2, "Inbound call, guided hardware reset")

# --- T8 -------------------------------------------------------------------
t = ticket(8, "Password reset link expires immediately",
           requester="tomas", status=4, priority=1, ttype="Problem", source=2,
           group="Tier 1 Support", responder=A1,
           tags=["auth", "password-reset"],
           files=["reset-flow-notes.docx"],
           body="<div><p>Hi,</p><p>Reset links sent to our staff expire the "
                "instant they are opened - <i>&quot;This link has "
                "expired&quot;</i>. Reproduction notes attached.</p>"
                "<p>Requesting the link twice works, which suggests the first "
                "token is being invalidated early.</p>"
                "<p>--<br>Tomás Ó Súilleabháin<br>IT Administrator</p></div>")
if t:
    msg(t, "<p>Hi Tomás,</p><p>Confirmed - your mail gateway pre-fetches links "
           "for scanning, which consumes the single-use token before the user "
           "clicks. We have moved your tenant to click-confirmed tokens.</p>")
    msg(t, "<p>That explains it. Tested with three accounts and all work now. "
           "Thanks.</p>", who="tomas")

# --- T9 -------------------------------------------------------------------
t = ticket(9, "Onboarding session for 12 new agents - scheduling",
           requester="sarah", status=3, priority=2, ttype="Question", source=1,
           group="Tier 1 Support", responder=A2,
           tags=["onboarding", "training"],
           cc=["hr@northgate.example.com", "training@northgate.example.com",
               "james.whitfield@northgate.example.com"],
           body="<div><p>Hi,</p><p>We are adding <b>12 agents</b> in September "
                "and would like a training session before they go live. Two "
                "hours, remote, ideally the week of the 14th.</p>"
                "<p>--<br>Sarah O'Brien<br>Retail Operations Manager</p></div>")
if t:
    msg(t, "<p>Hi Sarah,</p><p>Happy to run that. We have Tuesday 15th 10:00 "
           "or Thursday 17th 14:00 (both IST). Which suits? I will send "
           "calendar invites to the CC list once confirmed.</p>")

# --- T10 ------------------------------------------------------------------
t = ticket(10, "Upgrade from Growth to Pro - pricing confirmation",
           requester="priya", status=7, priority=3, ttype="Question", source=2,
           group="Billing & Accounts", responder=A2, ref=100250,
           tags=["upgrade", "pricing", "pre-sales"],
           body="<div><p>Hello,</p><p>We would like written confirmation of "
                "the <b>Growth to Pro</b> uplift for 25 agents, effective 1 "
                "October, including whether our current discount carries "
                "over.</p><p>Procurement needs this before they will raise "
                "the PO.</p>"
                "<p>--<br>Priya Raghavan<br>Finance Lead, Vertex Health</p></div>")
if t:
    msg(t, "<p>Hi Priya,</p><p>Pricing for the uplift is with our commercial "
           "desk for approval of the discount carry-over. I expect an answer "
           "within two working days and will confirm in writing here.</p>")
    msg(t, "<p>Understood - we can hold until Thursday.</p>", who="priya")
    msg(t, "<p>Internal: waiting on commercial desk sign-off. Ticket parked as "
           "Waiting on Third Party.</p>", private=True)

# ------------------------------------------------------------------ report
MANIFEST.write_text(json.dumps(made, indent=2, ensure_ascii=False), encoding="utf-8")
ids = [t["id"] for t in made["tickets"]]
print(f"\nmanifest -> {MANIFEST}")
print(f"created {len(ids)} tickets, tag '{TAG}'")
print(f"ticket ids for HDM 'Select records for Demo': "
      f"{','.join(str(i) for i in ids)}")
if _fail:
    print(f"\n{len(_fail)} call(s) did not succeed:")
    for f in _fail:
        print(f"  - {f}")
