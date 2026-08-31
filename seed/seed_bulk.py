"""Phase B - seed N realistic tickets for a volume + behaviour rehearsal.

Why this exists: the fidelity sets (zzz-deep, zzz-demo-10) prove every *shape*
a migrator has to survive, but they prove nothing about throughput, SLA clocks
under load, or how a thousand tickets behave once automations are switched on.
This builds the volume half.

Design notes that matter:

  * created_at cannot be back-dated (400 invalid_field), so every ticket lands
    today and the intended historical date goes into cf_original_created_date.
    That is exactly what the production migration will do, so this is a
    faithful rehearsal rather than a workaround.
  * due_by / fr_due_by are only accepted for FUTURE dates on SLA-timer statuses
    (Open / Pending / Waiting on *). A subset of Open tickets carries an
    explicit deadline so the carry-across path is exercised; the rest let the
    policy compute, so both behaviours are observable side by side.
  * Resumable. The manifest is written after every ticket, so a 429 storm, a
    laptop sleep or a Ctrl-C never duplicates and never restarts from zero.

    FD_SOURCE_DOMAIN=... FD_SOURCE_API_KEY=... py seed_bulk.py [count]
"""
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import _fixtures as fx
from fdapi import client_from_env, ConcurrentRun, FDError, Manifest

COUNT = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
SEED = 20260831
HERE = Path(__file__).parent
FILES = HERE / "_bulk_files"
MARKER = "zzz-bulk"

# POST /tickets/{id}/reply SENDS A REAL OUTBOUND EMAIL to the requester, and
# NO admin setting suppresses it. Verified live 31 Aug 2026: every requester
# notification on sdkinfinity8689 was already OFF when a seeded reply still
# reached the mail server and bounced. A reply is not a notification - it is
# the agent sending mail - so the notification screen does not govern it.
#
# The consequence for the real migration is the serious one: a tool that
# recreates agent replies through /reply will email the client's live
# customers no matter how the target is configured. Notes are the only
# email-safe way to reproduce a thread through the public API.
ALLOW_REPLY = os.environ.get("FD_ALLOW_REPLY") == "1"

random.seed(SEED)
c = client_from_env("SOURCE")
try:
    man = Manifest(str(HERE / "_bulk_manifest.json"))
except ConcurrentRun as e:
    raise SystemExit("REFUSING TO START: " + str(e))

# ---------------------------------------------------------------- content ---

SUBJECTS = [
    ("Order {ref} has not arrived", "Logistics", "Logistics & Delivery"),
    ("Wrong item delivered against order {ref}", "Logistics", "Logistics & Delivery"),
    ("Delivery window for {ref} needs changing", "Logistics", "Logistics & Delivery"),
    ("Package for {ref} arrived damaged", "Logistics", "Logistics & Delivery"),
    ("Invoice {ref} charged twice", "Billing", "Billing & Accounts"),
    ("請求 refund for cancelled order {ref}", "Billing", "Billing & Accounts"),
    ("VAT number missing from invoice {ref}", "Billing", "Billing & Accounts"),
    ("Payment failed but the amount was debited ({ref})", "Billing", "Billing & Accounts"),
    ("Subscription renewed unexpectedly - {ref}", "Billing", "Billing & Accounts"),
    ("Cannot sign in to the care portal", "Technical", "Technical Support"),
    ("Password reset link expires immediately", "Technical", "Technical Support"),
    ("Mobile app crashes on the medication screen", "Technical", "Technical Support"),
    ("API returns 502 on /v2/appointments", "Technical", "Technical Support"),
    ("Two-factor codes are not arriving by SMS", "Technical", "Technical Support"),
    ("Export of care records fails halfway", "Technical", "Technical Support"),
    ("Question about medication interaction", "Clinical", "Clinical Support"),
    ("Follow-up appointment needs rescheduling", "Clinical", "Clinical Support"),
    ("Care plan not visible to the assigned nurse", "Clinical", "Clinical Support"),
    ("Request for a copy of clinical notes", "Clinical", "Clinical Support"),
    ("Dosage instructions unclear on the label", "Clinical", "Clinical Support"),
    ("Complaint about the visiting carer on {ref}", "Complaint", "Complaints & Quality"),
    ("No response to my previous three emails", "Complaint", "Complaints & Quality"),
    ("Escalation: repeated missed home visits", "Complaint", "Complaints & Quality"),
    ("GDPR: request for all data held on me", "General", "Tier 1 Support"),
    ("GDPR: request for erasure", "General", "Tier 1 Support"),
    ("How do I add a family member to my account?", "General", "Tier 1 Support"),
    ("Update the address on my account", "General", "Tier 1 Support"),
    ("Change the primary contact for {ref}", "General", "Tier 1 Support"),
    ("Feature request: calendar sync", "General", "Tier 1 Support"),
    ("Ärende: kan inte logga in på portalen", "Technical", "Technical Support"),
]

SIGNATURES = [
    "<br><br>--<br>{name}<br>{company}<br>+358 40 {p}<br>",
    "<br><br>Kind regards,<br>{name}<br>",
    "<br><br>Ystävällisin terveisin,<br>{name}<br>{company}<br>",
    "<br><br>Sent from my iPhone<br>",
    "<br><br>Best,<br>{name}<br><br>"
    "<span style='font-size:10px;color:#888'>This email and any attachments "
    "are confidential and intended solely for the addressee.</span><br>",
    "",
]

OPENERS = [
    "<p>Hello,</p><p>{body}</p>",
    "<p>Hi team,</p><p>{body}</p>",
    "<p>Good morning,</p><p>{body}</p>",
    "<p>Hei,</p><p>{body}</p>",
    "<p>{body}</p>",
]

CUST_BODIES = [
    "I have been waiting since last week and there is still no update. "
    "Could someone please look into this and let me know where things stand?",
    "This is the second time this has happened. The first time it was "
    "resolved quickly, but it has now recurred and I need a permanent fix.",
    "Apologies for chasing, but this is becoming urgent for us. Our team "
    "cannot proceed until this is sorted out.",
    "Just to add some detail - the issue only happens when I use the mobile "
    "app. On desktop everything works exactly as expected.",
    "Thank you for the quick reply. That has partially helped, but one part "
    "of the problem is still outstanding.",
    "Could you confirm you have received my previous message? I have not "
    "had an acknowledgement yet.",
    "I have attached a screenshot showing exactly what I see on my screen.",
    "Sorry - ignore my last message, I found the setting. But I do still "
    "have a question about the billing side.",
    "Kiitos avusta! Tämä ratkaisi ongelman. Voitte sulkea tämän tiketin.",
    "That works now, thank you very much for your help. Please close this.",
]

AGENT_BODIES = [
    "<p>Thank you for getting in touch, and apologies for the delay.</p>"
    "<p>I have looked into this and can confirm the following:</p>"
    "<ul><li>The record was created correctly on our side.</li>"
    "<li>The delay appears to sit with the downstream provider.</li></ul>"
    "<p>I have escalated it and will update you within one working day.</p>",
    "<p>Thanks for the detail - that helps a lot.</p><p>Could you confirm "
    "which browser and version you are using, and whether the problem "
    "persists in a private window?</p>",
    "<p>Good news - this is now resolved. The change has been applied to "
    "your account and should be visible immediately.</p><p>Do let me know "
    "if you see anything unexpected.</p>",
    "<p>I have raised a refund request for you. Refunds usually reach the "
    "original payment method within 5-10 business days.</p>",
    "<p>Apologies for the experience. I have passed your feedback to the "
    "care team lead, who will be in touch directly.</p>",
    "<p>I am closing this one as resolved, but do reply here if it comes "
    "back and the ticket will reopen.</p>",
    "<blockquote style='border-left:2px solid #ccc;padding-left:8px;"
    "color:#666'>On Tue, you wrote:<br>&gt; I have been waiting since last "
    "week</blockquote><p>Following up on the above - the supplier has now "
    "confirmed dispatch.</p>",
]

NOTE_BODIES = [
    "<p>Checked the audit log - the record was updated by the overnight "
    "sync, not by the customer. Nothing to action on their side.</p>",
    "<p>@team this one is close to breaching, can someone pick it up if I "
    "am not back before 16:00?</p>",
    "<p>Confirmed with the logistics provider by phone. Reference "
    "{ref}. They will redeliver tomorrow.</p>",
    "<p>Customer has called twice about this. Handling with care - flag to "
    "the quality team if it escalates again.</p>",
    "<p>Refund approved by the finance lead. Processing now.</p>",
    "<p>Duplicate of an earlier ticket from the same household. Keeping "
    "this one as the master.</p>",
]

REGIONS = ["Finland", "Sweden", "Norway", "Denmark", "Other"]

# how many public/private messages hang off a ticket - a real helpdesk has a
# long tail, and the tail is where page-size bugs hide
THREAD_BUCKETS = [
    (0.10, (0, 0)),
    (0.60, (1, 3)),
    (0.22, (4, 7)),
    (0.06, (8, 15)),
    (0.02, (20, 42)),
]

STATUS_MIX = ([5] * 400 + [4] * 250 + [2] * 150 + [3] * 100
              + [6] * 70 + [7] * 30)
PRIORITY_MIX = [1] * 450 + [2] * 350 + [3] * 150 + [4] * 50
# GET /ticket_fields advertises 17 sources, but CREATE accepts only
# 1,2,3,7,9,11,10 - and 7 (Chat) is not even in the advertised list. WhatsApp
# (13), Web Chat (15) and SMS (22) are readable but not writable, so a
# migration cannot reproduce them. See MIGRATION_FIDELITY_SPEC section 2.7.
# source 10 (Outbound Email) is EXCLUDED on purpose. Creating a ticket with
# it means "an agent emailed the customer", so Freshdesk sends the description
# as real mail - proven live on tickets #119 and #131, which bounced while the
# other 22 tickets in the same run sent nothing. Second send path after /reply;
# neither is governed by the notification settings.
# source 11 (Ecommerce) is also out: "Cannot create/update tickets with
# source 11 without the ebay feature enabled". So of the 17 sources the API
# advertises, only FIVE are usable here - 1, 2, 3, 7, 9 - and which ones work
# is plan- and feature-dependent, not fixed. Probe, do not assume.
SOURCE_MIX = [1] * 540 + [2] * 210 + [3] * 130 + [7] * 90 + [9] * 30
TYPE_MIX = (["Question"] * 380 + ["Incident"] * 330 + ["Problem"] * 140
            + ["Feature Request"] * 90 + ["Refund"] * 60)

WINDOW_START = datetime(2023, 1, 1, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 8, 25, tzinfo=timezone.utc)


def pick_thread():
    r, acc = random.random(), 0.0
    for weight, span in THREAD_BUCKETS:
        acc += weight
        if r <= acc:
            return random.randint(*span)
    return 2


def historical_date():
    """Skewed towards recent - real ticket volume grows over time."""
    span = (WINDOW_END - WINDOW_START).days
    day = int(span * (random.random() ** 0.6))
    return WINDOW_START + timedelta(days=day,
                                    hours=random.randint(6, 20),
                                    minutes=random.randint(0, 59))


# ------------------------------------------------------------- attachments ---

def build_files():
    FILES.mkdir(exist_ok=True)
    made = {}
    fx.png(400, 260, FILES / "app-screenshot.png")
    fx.png(900, 600, FILES / "portal-error-large.png", accent=(40, 90, 200))
    fx.pdf("Invoice", ["Invoice INV-2026-0912", "Amount: EUR 148.00",
                       "Status: PAID"], FILES / "invoice.pdf")
    fx.pdf("Care report", ["Quarterly care summary", "Patient reference: R-8841"],
           FILES / "hoitoraportti-2026-Q3.pdf")
    fx.docx("Notes", ["Reproduction steps", "1. Open the app",
                      "2. Tap medication", "3. App closes"],
            FILES / "repro-notes.docx")
    fx.big_zip(FILES / "diagnostics-bundle.zip", mb=1.5)
    (FILES / "delivery-log.txt").write_text(
        "\n".join("2026-08-%02d 09:1%d dispatch scan OK" % (d, d % 10)
                  for d in range(1, 20)), encoding="utf-8")
    (FILES / "export-sample.csv").write_text(
        "id,name,status\n1,Aino Virtanen,active\n2,Eero Korhonen,paused\n",
        encoding="utf-8")
    for p in sorted(FILES.iterdir()):
        made[p.name] = (p.read_bytes(), fx.MIME.get(p.suffix,
                                                    "application/octet-stream"))
    return made


BLOBS = build_files()
SMALL = [n for n in BLOBS if n != "diagnostics-bundle.zip"]


def attach(n=1, allow_big=False):
    pool = list(BLOBS) if allow_big else SMALL
    names = random.sample(pool, min(n, len(pool)))
    return [(nm, BLOBS[nm][0], BLOBS[nm][1]) for nm in names]


# ------------------------------------------------------------------ lookup ---

print("reading config from the source instance ...", flush=True)
GROUPS = {g["name"]: g["id"] for g in c.paginate("/groups")}
AGENTS = [a["id"] for a in c.paginate("/agents")]
CONTACTS = [x for x in c.paginate("/contacts") if x.get("email")]
FIELDS = {f["name"] for f in c.get("/ticket_fields")}
print("   {} groups, {} agents, {} contacts, {} fields".format(
    len(GROUPS), len(AGENTS), len(CONTACTS), len(FIELDS)), flush=True)
if not CONTACTS:
    raise SystemExit("no contacts - run seed_config_bulk.py first")

HAS = lambda n: n in FIELDS  # noqa: E731


# ------------------------------------------------------------------- build ---

def make(idx):
    subj_t, category, group_name = random.choice(SUBJECTS)
    ref = "ORD-{}-{:05d}".format(random.randint(2023, 2026),
                                 random.randint(1, 99999))
    who = random.choice(CONTACTS)
    orig = historical_date()
    status = random.choice(STATUS_MIX)
    priority = random.choice(PRIORITY_MIX)
    n_msgs = pick_thread()

    company = (who.get("company_id") or "")
    sig = random.choice(SIGNATURES).format(
        name=who["name"], company=company,
        p="{:03d} {:04d}".format(random.randint(100, 999),
                                 random.randint(0, 9999)))
    body = random.choice(OPENERS).format(body=random.choice(CUST_BODIES)) + sig

    fields = {
        "subject": subj_t.format(ref=ref),
        "description": body,
        "email": who["email"],
        "status": status,
        "priority": priority,
        "source": random.choice(SOURCE_MIX),
        "type": random.choice(TYPE_MIX),
        "group_id": GROUPS.get(group_name),
        "tags": [MARKER, "bulk-{:04d}".format(idx),
                 category.lower(), "status-{}".format(status)],
    }

    # An assigned agent only makes sense on work that was actually picked up.
    if status != 2 or random.random() < 0.7:
        fields["responder_id"] = random.choice(AGENTS)

    custom = {}
    if HAS("cf_original_created_date"):
        custom["cf_original_created_date"] = orig.strftime("%Y-%m-%d")
    if HAS("cf_original_ticket_id"):
        custom["cf_original_ticket_id"] = 100000 + idx
    if HAS("cf_original_requester"):
        custom["cf_original_requester"] = who["email"]
    if HAS("cf_care_category"):
        custom["cf_care_category"] = category
    if HAS("cf_region"):
        custom["cf_region"] = random.choice(REGIONS)
    if HAS("cf_escalated_to_clinical"):
        custom["cf_escalated_to_clinical"] = (priority == 4
                                              and random.random() < 0.5)
    if HAS("cf_order_reference"):
        custom["cf_order_reference"] = ref
    if custom:
        fields["custom_fields"] = custom

    # cc_emails is create-only - a PUT with it returns 400 invalid_field
    if random.random() < 0.08:
        fields["cc_emails"] = [random.choice(CONTACTS)["email"]
                               for _ in range(random.randint(1, 3))]

    # SLA: only future deadlines on timer statuses are accepted. Give a slice
    # of the live tickets an explicit deadline so the carry path is exercised,
    # and leave the rest for the policy to compute.
    # Only Open carries an SLA timer on this instance - Pending and the
    # Waiting-on statuses reject due_by with incompatible_field.
    if status == 2 and random.random() < 0.45:
        hrs = random.choice([2, 6, 12, 24, 48, 96, 240])
        due = datetime.now(timezone.utc) + timedelta(hours=hrs)
        fields["due_by"] = due.strftime("%Y-%m-%dT%H:%M:%SZ")
        fields["fr_due_by"] = (
            datetime.now(timezone.utc)
            + timedelta(hours=max(1, hrs // 3))).strftime("%Y-%m-%dT%H:%M:%SZ")

    files = attach(random.choice([1, 1, 2]),
                   allow_big=random.random() < 0.02) \
        if random.random() < 0.18 else None

    return fields, files, n_msgs, who, ref


def thread(tid, n, who, ref):
    """Alternating customer / agent traffic with private notes mixed in.

    Message kinds and whether they generate outbound email:
        private note          - never
        incoming public note  - never (it is inbound traffic)
        public note           - only if the admin enabled that notification
        reply                 - ALWAYS, no opt-out
    """
    calls = 0
    for i in range(n):
        files = attach(1) if random.random() < 0.06 else None
        try:
            if random.random() < 0.22:
                c.post("/tickets/{}/notes".format(tid),
                       {"body": random.choice(NOTE_BODIES).format(ref=ref),
                        "private": True, "notify_emails": []}, files=files)
            elif i % 2 == 0:
                body = random.choice(AGENT_BODIES)
                if ALLOW_REPLY:
                    c.post("/tickets/{}/reply".format(tid),
                           {"body": body}, files=files)
                else:
                    # Public note, not private: the "Agent Adds Comment to
                    # Ticket" requester notification is OFF on this instance,
                    # so a public note is email-safe and keeps the agent side
                    # of the thread customer-visible.
                    c.post("/tickets/{}/notes".format(tid),
                           {"body": body, "private": False,
                            "notify_emails": []}, files=files)
            else:
                # The public API attributes every note to the key owner, so a
                # customer-voiced message has to be an incoming-flavoured note.
                c.post("/tickets/{}/notes".format(tid),
                       {"body": "<p><em>[{}]</em></p>{}".format(
                           who["name"],
                           random.choice(OPENERS).format(
                               body=random.choice(CUST_BODIES))),
                        "private": False, "incoming": True,
                        "notify_emails": []}, files=files)
            calls += 1
        except FDError as e:
            print("      msg {} on #{}: {} {}".format(i, tid, e.code,
                                                      e.body[:120]))
    return calls


def main():
    done = sum(1 for k in man.data if k.startswith("bulk-"))
    print("resuming: {} of {} already seeded\n".format(done, COUNT), flush=True)
    start = datetime.now()
    made = 0
    for idx in range(1, COUNT + 1):
        key = "bulk-{:04d}".format(idx)
        if man.has(key):
            continue
        fields, files, n_msgs, who, ref = make(idx)
        try:
            t = c.post("/tickets", fields, files=files)
        except FDError as e:
            print("   #{} create failed: {} {}".format(idx, e.code,
                                                       e.body[:200]))
            continue
        tid = t["id"]
        sent = thread(tid, n_msgs, who, ref)

        if random.random() < 0.15:
            try:
                c.post("/tickets/{}/time_entries".format(tid), {
                    "agent_id": random.choice(AGENTS),
                    "time_spent": "{}:{:02d}".format(random.randint(0, 3),
                                                     random.choice([0, 15, 30, 45])),
                    "billable": random.random() < 0.6,
                    "note": "Work logged during handling",
                    "executed_at": historical_date().strftime(
                        "%Y-%m-%dT%H:%M:%SZ")})
            except FDError:
                pass

        man.put(key, {"id": tid, "msgs": sent, "status": fields["status"]})
        made += 1
        if idx % 25 == 0:
            el = (datetime.now() - start).total_seconds() / 60
            tpm = made / max(el, 0.01)
            left = COUNT - idx
            print("   {}/{}  last #{}  {:.1f} tickets/min  "
                  "{:.0f} calls/min  ~{:.0f} min left"
                  .format(idx, COUNT, tid, tpm,
                          c.calls / max(el, 0.01), left / max(tpm, 0.01)),
                  flush=True)

    print("\nDONE - {} API calls, {} throttle waits".format(c.calls,
                                                            c.throttled))
    print("manifest: {}".format(man.path))


if __name__ == "__main__":
    try:
        main()
    finally:
        man.release()
