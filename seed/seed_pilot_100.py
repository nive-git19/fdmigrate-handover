"""Seed the SOURCE account with the 100-ticket 'worst case' pilot dataset.

Covers every dimension the migration must prove:
  - attachments on the ticket description AND on replies (PDF/PNG/DOCX/MP3/CSV,
    multi-attachment tickets, one ~6MB file for the oversize-cap test)
  - full conversation history, faithful to real usage: customer replies seeded
    as INCOMING notes (contact user_id - the only API way to add a customer
    message), agent replies as REAL /reply posts, internal notes private.
    Requester addresses are fake (@example.com / seeded contacts), so reply
    emails bounce harmlessly; still, turn source notifications off first.
  - status spread 25/25/25/25 across Open/Pending/Resolved/Closed (the batch key)
  - every ticket assigned to an agent (rotating across whoever exists) + most to a group
  - mixed requesters (existing contacts + new auto-created ones), CCs on some

All tickets are tagged  zzz-pilot-100  for cleanup.
Writes _pilot_manifest.json (ticket ids + expected counts) for verification.

Usage:
    export FD_SOURCE_DOMAIN=https://yourtrial.freshdesk.com
    export FD_SOURCE_API_KEY=...
    python -X utf8 seed/seed_pilot_100.py

WARNING: this WRITES 100 tickets. Point it at a throwaway test account only.
Turn the source account's outbound email OFF first - seeded requesters are fake
addresses and the bounces can trip Freshdesk's outgoing-email block.
"""
from __future__ import annotations

import base64
import io
import json
import os
import random
import time
import zipfile
from pathlib import Path

import requests

_DOMAIN = os.environ.get("FD_SOURCE_DOMAIN", "").strip().rstrip("/")
if not _DOMAIN:
    raise SystemExit(
        "Set FD_SOURCE_DOMAIN to the account you want to SEED, e.g.\n"
        "  export FD_SOURCE_DOMAIN=https://yourtrial.freshdesk.com\n"
        "  export FD_SOURCE_API_KEY=...\n"
        "Point this at a TEST account only - it writes 100 tickets.")
if not _DOMAIN.startswith("http"):
    _DOMAIN = "https://" + _DOMAIN

BASE = f"{_DOMAIN}/api/v2"
try:
    AUTH = (os.environ["FD_SOURCE_API_KEY"], "X")
except KeyError:
    raise SystemExit("Set FD_SOURCE_API_KEY (admin API key for the source account).")
TAG = "zzz-pilot-100"
N = 100
FILES_DIR = Path(__file__).parent / "_pilot_files"
MANIFEST = Path(__file__).parent / "_pilot_manifest.json"

random.seed(42)  # reproducible dataset

# ---------------------------------------------------------------- attachments
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQ"
    "DwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

MINIMAL_PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
               b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
               b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
               b"xref\n0 4\n0000000000 65535 f \ntrailer<</Size 4/Root 1 0 R>>\n"
               b"startxref\n0\n%%EOF\n")


def make_docx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org'
                   '/package/2006/content-types"><Default Extension="rels" ContentType='
                   '"application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/vnd.openxmlformats'
                   '-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxml'
                   'formats.org/package/2006/relationships"><Relationship Id="rId1" '
                   'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                   'relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml",
                   '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxml'
                   'formats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'
                   'Pilot migration test document.</w:t></w:r></w:p></w:body></w:document>')
    return buf.getvalue()


def build_files():
    FILES_DIR.mkdir(exist_ok=True)
    rnd = random.Random(7)
    specs = {
        "incident_report.pdf": MINIMAL_PDF,
        "error_screenshot.png": PNG_1PX,
        "meeting_notes.docx": make_docx(),
        "call_recording.mp3": b"ID3\x03\x00\x00\x00\x00\x00\x00" + bytes(rnd.getrandbits(8) for _ in range(300_000)),
        "data_export.csv": b"order_id,amount,status\n1001,49.90,paid\n1002,120.00,refunded\n",
        "big_call_recording.mp3": b"ID3\x03\x00\x00\x00\x00\x00\x00" + os.urandom(6 * 1024 * 1024),
    }
    for name, content in specs.items():
        p = FILES_DIR / name
        if not p.exists() or p.stat().st_size != len(content):
            p.write_bytes(content)
    return specs


MIME = {".pdf": "application/pdf", ".png": "image/png", ".csv": "text/csv",
        ".mp3": "audio/mpeg",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


def att_tuple(name: str):
    p = FILES_DIR / name
    return ("attachments[]", (name, open(p, "rb"), MIME[p.suffix]))


# ------------------------------------------------------------------ API layer
session = requests.Session()


def call(method: str, path: str, **kw):
    """Paced call with 429 retry. Trial cap is 50 req/min."""
    for attempt in range(5):
        r = session.request(method, f"{BASE}{path}", auth=AUTH, timeout=120, **kw)
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", "30"))
            print(f"    429 rate-limited, sleeping {wait}s...", flush=True)
            time.sleep(wait)
            continue
        remaining = r.headers.get("X-RateLimit-Remaining")
        if remaining is not None and float(remaining) < 4:
            time.sleep(15)  # let the window refill before we slam into 429s
        else:
            time.sleep(1.0)  # ~50/min pacing
        return r
    return r


# ------------------------------------------------------------------- content
SUBJECTS = [
    "Cannot log in to customer portal", "Invoice attachment is corrupted",
    "Refund not processed after 10 days", "Error 502 when uploading documents",
    "Feature request: export tickets to CSV", "Mobile app crashes on startup",
    "Wrong shipping address on order", "Password reset email never arrives",
    "API integration returning 401", "Duplicate charges on credit card",
    "Report dashboard shows zero data", "Call recording quality issue",
    "SLA breach on premium account", "Account migration data missing",
    "Webhook delivery failing intermittently", "Custom field values not saving",
]

CUSTOMER_LINES = [
    "Hi team, this is still not working on my end. I've attached what I'm seeing.",
    "Any update on this? It's been blocking our team since yesterday.",
    "Thanks for the quick response. I tried that but the error persists.",
    "That worked partially - the first step succeeds but the export still fails.",
    "Please treat this as urgent, we have a customer demo tomorrow.",
    "Confirming the issue is resolved on our side now. Thank you!",
]

AGENT_LINES = [
    "Thanks for reaching out. We've reproduced the issue and escalated it to engineering.",
    "Could you try clearing the cache and attempting the upload again?",
    "We've deployed a fix to your account. Please verify and let us know.",
    "I've attached the corrected invoice and the diagnostic steps we ran.",
    "Following up - our engineering team identified the root cause in the sync job.",
    "Glad to hear it's working. I'll mark this ticket as resolved.",
]

INTERNAL_NOTES = [
    "Internal: customer is on the Enterprise plan, prioritise. Ref JIRA ENG-4412.",
    "Internal: same root cause as ticket batch from last week's deploy.",
    "Internal: refund approved by finance, processing via Stripe dashboard.",
    "Internal: escalated to L2, awaiting response. Do not resolve until confirmed.",
]

NEW_REQUESTERS = [
    ("Priya Raman", "priya.raman.pilot@example.com"),
    ("Tom Becker", "tom.becker.pilot@example.com"),
    ("Lena Fischer", "lena.fischer.pilot@example.com"),
    ("Marcus Webb", "marcus.webb.pilot@example.com"),
]


def main():
    build_files()

    # Live source inventory: agents, groups, existing contacts.
    agents = call("GET", "/agents?per_page=100").json()
    groups = call("GET", "/groups?per_page=100").json()
    contacts = call("GET", "/contacts?per_page=100").json()
    agent_pool = [(a["id"], (a.get("contact") or {}).get("email", "?")) for a in agents]
    group_pool = [g["id"] for g in groups]
    contact_pool = [(c["id"], c["email"]) for c in contacts if c.get("email")]
    print(f"source inventory: {len(agent_pool)} agents, {len(group_pool)} groups, "
          f"{len(contact_pool)} contacts", flush=True)
    if not agent_pool or not contact_pool:
        raise SystemExit("Need at least 1 agent and 1 contact in the source.")

    small_files = ["incident_report.pdf", "error_screenshot.png",
                   "meeting_notes.docx", "call_recording.mp3", "data_export.csv"]
    manifest, failed = [], []

    for i in range(1, N + 1):
        final_status = [2, 3, 4, 5][(i - 1) % 4]           # 25 each
        agent_id = agent_pool[(i - 1) % len(agent_pool)][0]
        group_id = group_pool[(i - 1) % len(group_pool)] if group_pool and i % 5 != 0 else None

        # 70% existing contacts, 30% new (auto-created by Freshdesk on ticket POST)
        if i % 10 < 7:
            requester_email = contact_pool[(i - 1) % len(contact_pool)][1]
        else:
            requester_email = NEW_REQUESTERS[(i - 1) % len(NEW_REQUESTERS)][1]

        # description attachments
        desc_files = []
        if i == 50:
            desc_files = ["big_call_recording.mp3"]
        elif i % 10 == 0:
            desc_files = random.sample(small_files, 3)
        elif i % 3 == 0:
            desc_files = [small_files[(i // 3) % len(small_files)]]

        subject = f"[PILOT-{i:03d}] {SUBJECTS[(i - 1) % len(SUBJECTS)]}"
        description = (f"<h3>{SUBJECTS[(i - 1) % len(SUBJECTS)]}</h3>"
                       f"<p>Pilot ticket <b>PILOT-{i:03d}</b>. Steps to reproduce:</p>"
                       f"<ol><li>Open the portal</li><li>Navigate to billing</li>"
                       f"<li>Observe the failure</li></ol>"
                       f"<p>Environment: production &middot; severity {1 + i % 4}</p>")

        fields = {
            "email": requester_email, "subject": subject, "description": description,
            "status": 2, "priority": 1 + (i - 1) % 4,
            "type": ["Question", "Incident", "Problem", "Feature Request"][(i - 1) % 4],
            "responder_id": agent_id,
        }
        if group_id:
            fields["group_id"] = group_id

        if desc_files:
            data = [(k, str(v)) for k, v in fields.items()]
            data.append(("tags[]", TAG))
            if i % 7 == 0:
                data.append(("cc_emails[]", "cc.watcher.pilot@example.com"))
            files = [att_tuple(f) for f in desc_files]
            r = call("POST", "/tickets", data=data, files=files)
            for _, (_, fh, _) in files:
                fh.close()
        else:
            fields["tags"] = [TAG]
            if i % 7 == 0:
                fields["cc_emails"] = ["cc.watcher.pilot@example.com"]
            r = call("POST", "/tickets", json=fields)

        if r.status_code != 201:
            failed.append({"i": i, "step": "create", "code": r.status_code,
                           "body": r.text[:300]})
            print(f"  [{i:3d}] CREATE FAILED {r.status_code}: {r.text[:120]}", flush=True)
            continue
        t = r.json()
        tid, requester_id = t["id"], t["requester_id"]

        # ---- conversation history: customer <-> agent + internal notes -------
        # customer message -> INCOMING note (contact author); agent message ->
        # REAL /reply; internal -> private note. Mirrors real ticket threads so
        # the migration's as-is replication has all three kinds to reproduce.
        n_msgs = 3 + (i % 6)                                   # 3..8
        note_att_placed = reply_att_placed = False
        notes_ok = 0
        for m in range(n_msgs):
            files = None
            if m % 3 == 2:                                     # every 3rd = internal
                body = INTERNAL_NOTES[m % len(INTERNAL_NOTES)]
                path = f"/tickets/{tid}/notes"
                payload = {"body": f"<p>{body}</p>", "private": True,
                           "user_id": agent_pool[(i + m) % len(agent_pool)][0]}
            elif m % 2 == 0:                                   # customer reply
                body = CUSTOMER_LINES[(i + m) % len(CUSTOMER_LINES)]
                path = f"/tickets/{tid}/notes"
                payload = {"body": f"<p>{body}</p>", "private": False,
                           "incoming": True, "user_id": requester_id}
                if i % 4 == 0 and not note_att_placed:
                    files = [att_tuple(small_files[(i + m) % len(small_files)])]
                    note_att_placed = True
            else:                                              # agent reply (real)
                body = AGENT_LINES[(i + m) % len(AGENT_LINES)]
                path = f"/tickets/{tid}/reply"
                payload = {"body": f"<p>{body}</p>",
                           "user_id": agent_pool[(i + m) % len(agent_pool)][0]}
                if i % 8 == 0 and not reply_att_placed:
                    files = [att_tuple(small_files[(i + m + 1) % len(small_files)])]
                    reply_att_placed = True

            if files:
                data = [("body", payload["body"]),
                        ("user_id", str(payload["user_id"]))]
                if "private" in payload:
                    data.append(("private", "true" if payload["private"] else "false"))
                if payload.get("incoming"):
                    data.append(("incoming", "true"))
                nr = call("POST", path, data=data, files=files)
                for _, (_, fh, _) in files:
                    fh.close()
            else:
                nr = call("POST", path, json=payload)
            if nr.status_code == 201:
                notes_ok += 1
            else:
                failed.append({"i": i, "step": f"msg{m}", "code": nr.status_code,
                               "body": nr.text[:300]})

        # ---- final status (notes can reopen, so set it last) -----------------
        if final_status != 2:
            sr = call("PUT", f"/tickets/{tid}", json={"status": final_status})
            if sr.status_code != 200:
                failed.append({"i": i, "step": "status", "code": sr.status_code,
                               "body": sr.text[:300]})

        manifest.append({
            "i": i, "ticket_id": tid, "subject": subject, "status": final_status,
            "requester": requester_email, "agent_id": agent_id, "group_id": group_id,
            "desc_attachments": desc_files, "notes": notes_ok,
            "note_attachment": note_att_placed, "reply_attachment": reply_att_placed,
        })
        if i % 10 == 0:
            print(f"  [{i:3d}/{N}] seeded (last ticket id {tid}, {notes_ok} notes)",
                  flush=True)

    MANIFEST.write_text(json.dumps({"created": manifest, "failed": failed}, indent=1),
                        encoding="utf-8")
    by_status = {}
    for m in manifest:
        by_status[m["status"]] = by_status.get(m["status"], 0) + 1
    total_notes = sum(m["notes"] for m in manifest)
    total_desc_atts = sum(len(m["desc_attachments"]) for m in manifest)
    total_note_atts = sum(1 for m in manifest if m["note_attachment"])
    total_reply_atts = sum(1 for m in manifest if m["reply_attachment"])
    print("\n===== SEED SUMMARY =====")
    print(f"tickets created : {len(manifest)}/{N}")
    print(f"by status       : {by_status}  (2=Open 3=Pending 4=Resolved 5=Closed)")
    print(f"conversations   : {total_notes}")
    print(f"desc attachments: {total_desc_atts} (incl. one ~6MB on PILOT-050)")
    print(f"note attachments: {total_note_atts} | reply attachments: {total_reply_atts}")
    print(f"failures        : {len(failed)} (see _pilot_manifest.json)")
    print(f"manifest        : {MANIFEST}")


if __name__ == "__main__":
    main()
