"""Verify the bulk dataset actually contains what the seeder intended.

Read-only. Cheap by design: ticket-level facts come from the LIST endpoint
(~6 calls for 500 tickets) rather than per-ticket detail, thread depth comes
from the manifest, and only attachments need a sample of detail calls - the
whole point being that a 50 req/min ceiling makes a naive per-ticket audit
take twenty minutes.

    FD_SOURCE_DOMAIN=... FD_SOURCE_API_KEY=... py verify_bulk.py [sample]
"""
import collections
import json
import sys
from pathlib import Path

from fdapi import client_from_env

SAMPLE = int(sys.argv[1]) if len(sys.argv) > 1 else 90
HERE = Path(__file__).parent
MARKER = "zzz-bulk"

c = client_from_env("SOURCE")
man = json.loads((HERE / "_bulk_manifest.json").read_text(encoding="utf-8"))

STATUS = {2: "Open", 3: "Pending", 4: "Resolved", 5: "Closed",
          6: "Waiting on Customer", 7: "Waiting on Third Party"}
PRIORITY = {1: "Low", 2: "Medium", 3: "High", 4: "Urgent"}
SOURCE = {1: "Email", 2: "Portal", 3: "Phone", 7: "Chat",
          9: "Feedback Widget", 10: "Outbound Email", 11: "Ecommerce"}


def bar(n, total, width=30):
    filled = int(width * n / total) if total else 0
    return "#" * filled + "." * (width - filled)


def dist(title, counter, total, names=None):
    print("\n" + title)
    for k, n in counter.most_common():
        label = names.get(k, k) if names else k
        print("   {:<22} {:>4}  {:>5.1f}%  {}".format(
            str(label)[:22], n, 100 * n / total, bar(n, total)))


print("reading every bulk ticket from the list endpoint ...", flush=True)
live = [t for t in c.paginate("/tickets?updated_since=2000-01-01T00:00:00Z")
        if MARKER in (t.get("tags") or [])]
total = len(live)
print("   {} tickets carry the {} marker  ({} API calls so far)".format(
    total, MARKER, c.calls))

# ---- reconciliation ------------------------------------------------------
known = {v["id"] for v in man.values()}
live_ids = {t["id"] for t in live}
print("\n=== RECONCILIATION")
print("   manifest entries      : {}".format(len(man)))
print("   live marker tickets   : {}".format(total))
print("   in manifest, not live : {}".format(sorted(known - live_ids)[:10] or "none"))
print("   live, not in manifest : {}".format(sorted(live_ids - known)[:10] or "none"))

groups = {g["id"]: g["name"] for g in c.paginate("/groups")}
agents = {a["id"]: a["contact"]["name"] for a in c.paginate("/agents")}

dist("=== STATUS", collections.Counter(t["status"] for t in live), total, STATUS)
dist("=== PRIORITY", collections.Counter(t["priority"] for t in live), total, PRIORITY)
dist("=== SOURCE", collections.Counter(t["source"] for t in live), total, SOURCE)
dist("=== TYPE", collections.Counter(t.get("type") for t in live), total)
dist("=== GROUP", collections.Counter(
    groups.get(t.get("group_id"), "(none)") for t in live), total)
dist("=== RESPONDER", collections.Counter(
    agents.get(t.get("responder_id"), "(unassigned)") for t in live), total)

# ---- the trap that bit us: responder silently dropped ---------------------
unassigned = [t for t in live if not t.get("responder_id")]
print("\n=== RESPONDER INTEGRITY")
print("   unassigned: {} of {} ({:.1f}%)".format(
    len(unassigned), total, 100 * len(unassigned) / total))
print("   NOTE: tickets seeded before all 7 groups had members lost their")
print("         responder silently - Freshdesk returns 200 and nulls it.")

# ---- SLA -----------------------------------------------------------------
print("\n=== SLA DEADLINES")
open_t = [t for t in live if t["status"] == 2]
with_due = [t for t in live if t.get("due_by")]
open_due = [t for t in open_t if t.get("due_by")]
print("   Open tickets            : {}".format(len(open_t)))
print("   carrying an explicit due_by : {} of {} open ({:.0f}%)".format(
    len(open_due), len(open_t), 100 * len(open_due) / max(len(open_t), 1)))
print("   any ticket with due_by  : {}".format(len(with_due)))
nonopen_due = [t for t in with_due if t["status"] != 2]
print("   due_by on non-Open      : {}  (policy-computed, not seeded)".format(
    len(nonopen_due)))

# ---- custom fields -------------------------------------------------------
print("\n=== CUSTOM FIELD FILL RATE")
fields = collections.Counter()
for t in live:
    for k, v in (t.get("custom_fields") or {}).items():
        if v not in (None, ""):
            fields[k] += 1
for k, n in sorted(fields.items()):
    print("   {:<32} {:>4}  {:>5.1f}%".format(k, n, 100 * n / total))

dates = [t["custom_fields"]["cf_original_created_date"] for t in live
         if (t.get("custom_fields") or {}).get("cf_original_created_date")]
if dates:
    print("   original-date span: {} .. {}".format(min(dates), max(dates)))
    yrs = collections.Counter(d[:4] for d in dates)
    print("   by year: " + "  ".join("{}={}".format(y, n)
                                     for y, n in sorted(yrs.items())))

# ---- thread depth from the manifest (free) -------------------------------
print("\n=== THREAD DEPTH  (from manifest, all {} tickets)".format(len(man)))
msgs = [v["msgs"] for v in man.values()]
buckets = collections.Counter()
for m in msgs:
    buckets["0" if m == 0 else "1-3" if m <= 3 else "4-7" if m <= 7
            else "8-15" if m <= 15 else "16+"] += 1
for k in ("0", "1-3", "4-7", "8-15", "16+"):
    n = buckets.get(k, 0)
    print("   {:<6} {:>4}  {:>5.1f}%  {}".format(
        k, n, 100 * n / len(msgs), bar(n, len(msgs))))
print("   total messages: {}   mean {:.1f}   max {}".format(
    sum(msgs), sum(msgs) / len(msgs), max(msgs)))

# ---- attachments: needs detail calls, so sample --------------------------
print("\n=== ATTACHMENTS  (sample of {} tickets)".format(SAMPLE))
import random
random.seed(1)
sample = random.sample(live, min(SAMPLE, total))
desc_att = conv_att = with_any = 0
names = collections.Counter()
for t in sample:
    d = c.get("/tickets/{}".format(t["id"]))
    a = d.get("attachments") or []
    convs = c.paginate("/tickets/{}/conversations".format(t["id"]))
    ca = [x for cv in convs for x in (cv.get("attachments") or [])]
    desc_att += len(a)
    conv_att += len(ca)
    if a or ca:
        with_any += 1
    for x in a + ca:
        names[x["name"]] += 1
print("   tickets carrying files : {} of {}  ({:.0f}%)".format(
    with_any, len(sample), 100 * with_any / len(sample)))
print("   on description {} | on replies/notes {}".format(desc_att, conv_att))
for n, k in names.most_common(10):
    print("      {:<34} {}".format(n, k))

print("\n{} API calls total".format(c.calls))
