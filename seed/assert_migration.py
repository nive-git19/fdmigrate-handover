"""Assert that a migration actually preserved the seeded datasets.

The test cases in docs/ are checklists a human ticks. This is the same checks,
executable. Point it at a source and target after any migration run - ours or
HDM's - and it compares every seeded ticket field by field and tells you what
broke.

    export FD_SOURCE_DOMAIN=https://source.freshdesk.com
    export FD_SOURCE_API_KEY=...
    export FD_TARGET_DOMAIN=https://target.freshdesk.com
    export FD_TARGET_API_KEY=...

    python -X utf8 seed/assert_migration.py              # all seeded datasets
    python -X utf8 seed/assert_migration.py --set deep   # just zzz-deep
    python -X utf8 seed/assert_migration.py --csv out.csv

Exit code is 0 only when every check passes, so it drops straight into CI.

Matching: a target ticket is found by the `fd-migration-{source_id}` marker tag
that fdmigrate writes; failing that, by exact subject. The report says which
route matched, because "matched by subject" is itself worth knowing.

READ-ONLY. It never writes to either account.
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).parent
MANIFESTS = {"demo": HERE / "_demo_manifest.json",
             "deep": HERE / "_deep_manifest.json",
             "edge": HERE / "_edge_manifest.json"}

ARG = sys.argv[1:]
ONLY = None
if "--set" in ARG:
    ONLY = ARG[ARG.index("--set") + 1]
CSV_OUT = ARG[ARG.index("--csv") + 1] if "--csv" in ARG else None
THROTTLE = float(os.environ.get("FD_THROTTLE", "1.25"))


def _acct(prefix: str):
    dom = os.environ.get(f"FD_{prefix}_DOMAIN", "").strip().rstrip("/")
    key = os.environ.get(f"FD_{prefix}_API_KEY", "").strip()
    if not dom or not key:
        raise SystemExit(
            f"Set FD_{prefix}_DOMAIN and FD_{prefix}_API_KEY.\n"
            "  export FD_SOURCE_DOMAIN=https://source.freshdesk.com\n"
            "  export FD_SOURCE_API_KEY=...\n"
            "  export FD_TARGET_DOMAIN=https://target.freshdesk.com\n"
            "  export FD_TARGET_API_KEY=...")
    if not dom.startswith("http"):
        dom = "https://" + dom
    return dom, (key, "X")


SRC_DOM, SRC_AUTH = _acct("SOURCE")
TGT_DOM, TGT_AUTH = _acct("TARGET")

STATUS = {2: "Open", 3: "Pending", 4: "Resolved", 5: "Closed",
          6: "Waiting on Customer", 7: "Waiting on Third Party"}
PRIORITY = {1: "Low", 2: "Medium", 3: "High", 4: "Urgent"}
SOURCE = {1: "Email", 2: "Portal", 3: "Phone", 7: "Chat", 9: "Feedback Widget",
          10: "Outbound Email"}


def get(dom, auth, path, **params):
    """No per_page here on purpose: the single-ticket DETAIL endpoint rejects
    it with 400 invalid_field. Only list endpoints accept it - see paginate()."""
    for _ in range(4):
        r = requests.get(f"{dom}/api/v2{path}", auth=auth,
                         params=params or None, timeout=90)
        if r.status_code == 429:
            time.sleep(int(r.headers.get("Retry-After", 30)) + 1)
            continue
        time.sleep(THROTTLE)
        return r.json() if r.ok else None
    return None


def paginate(dom, auth, path, **params):
    """ALWAYS use this for conversations. `include=conversations` on the ticket
    detail silently caps at 10, and this endpoint defaults to 30 - a 40-message
    thread reads as 10 or 30 with HTTP 200 and no warning."""
    out, page = [], 1
    while page <= 25:
        b = get(dom, auth, path, page=page, per_page=100, **params)
        if not isinstance(b, list) or not b:
            break
        out += b
        if len(b) < 100:
            break
        page += 1
    return out


def ticket_bundle(dom, auth, tid):
    t = get(dom, auth, f"/tickets/{tid}")
    if not t or "id" not in t:
        return None
    t["_conversations"] = paginate(dom, auth, f"/tickets/{tid}/conversations")
    t["_time_entries"] = paginate(dom, auth, f"/tickets/{tid}/time_entries")
    return t


def attachments(t):
    """(name, size) for description-level AND conversation-level files. Tools
    that only read t['attachments'] drop the second kind silently."""
    out = [(a["name"], a["size"]) for a in (t.get("attachments") or [])]
    for c in t.get("_conversations", []):
        out += [(a["name"], a["size"]) for a in (c.get("attachments") or [])]
    return sorted(out)


# ------------------------------------------------------------------ lookups
print(f"source  {SRC_DOM}\ntarget  {TGT_DOM}\n")
print("indexing target ...")
tgt_index = paginate(TGT_DOM, TGT_AUTH, "/tickets")
by_marker, by_subject = {}, {}
for t in tgt_index:
    for tag in (t.get("tags") or []):
        if tag.startswith("fd-migration-"):
            by_marker.setdefault(tag[len("fd-migration-"):], []).append(t)
    by_subject.setdefault(t["subject"], []).append(t)
print(f"  {len(tgt_index)} target tickets, "
      f"{len(by_marker)} carrying fd-migration-* markers\n")

src_groups = {g["id"]: g["name"] for g in paginate(SRC_DOM, SRC_AUTH, "/groups")}
tgt_groups = {g["id"]: g["name"] for g in paginate(TGT_DOM, TGT_AUTH, "/groups")}
src_agents = {a["id"]: (a.get("contact") or {}).get("email")
              for a in paginate(SRC_DOM, SRC_AUTH, "/agents")}
tgt_agents = {a["id"]: (a.get("contact") or {}).get("email")
              for a in paginate(TGT_DOM, TGT_AUTH, "/agents")}


def contact_email(dom, auth, cid, cache={}):
    if cid is None:
        return None
    k = (dom, cid)
    if k not in cache:
        c = get(dom, auth, f"/contacts/{cid}")
        cache[k] = (c or {}).get("email")
    return cache[k]


# ------------------------------------------------------------------- cases
cases = []
for name, path in MANIFESTS.items():
    if ONLY and name != ONLY:
        continue
    if not path.exists():
        continue
    man = json.loads(path.read_text(encoding="utf-8"))
    if man.get("domain", "").rstrip("/") != SRC_DOM:
        print(f"! {path.name} was seeded into {man.get('domain')}, "
              f"not the source - skipping")
        continue
    entries = man.get("tickets")
    if isinstance(entries, dict):
        for case, info in entries.items():
            cases.append((name, case, info["id"]))
    else:
        for info in entries:
            cases.append((name, f"T{info['n']}", info["id"]))

if not cases:
    raise SystemExit(
        "No seed manifests found for this source. Run a seeder first:\n"
        "  python -X utf8 seed/seed_demo_10.py\n"
        "  python -X utf8 seed/seed_deep.py")

print(f"checking {len(cases)} seeded ticket(s) "
      f"(~{len(cases) * 6} API calls, throttled)\n")

rows = []


def check(dataset, case, sid, tid, name, expected, actual):
    ok = expected == actual
    rows.append({"dataset": dataset, "case": case, "source": sid,
                 "target": tid, "check": name, "expected": expected,
                 "actual": actual, "result": "PASS" if ok else "FAIL"})
    return ok


for dataset, case, sid in cases:
    s = ticket_bundle(SRC_DOM, SRC_AUTH, sid)
    if not s:
        rows.append({"dataset": dataset, "case": case, "source": sid,
                     "target": "-", "check": "source readable",
                     "expected": "ticket", "actual": "not found",
                     "result": "FAIL"})
        print(f"  {case:<7} #{sid:<4} SOURCE NOT FOUND")
        continue

    cands = by_marker.get(str(sid)) or by_subject.get(s["subject"]) or []
    route = ("marker" if by_marker.get(str(sid))
             else ("subject" if cands else "-"))
    if not cands:
        rows.append({"dataset": dataset, "case": case, "source": sid,
                     "target": "-", "check": "migrated",
                     "expected": "present on target", "actual": "MISSING",
                     "result": "FAIL"})
        print(f"  {case:<7} #{sid:<4} -> MISSING on target")
        continue
    if len(cands) > 1:
        rows.append({"dataset": dataset, "case": case, "source": sid,
                     "target": ",".join(str(c["id"]) for c in cands),
                     "check": "no duplicates", "expected": 1,
                     "actual": len(cands), "result": "FAIL"})

    t = ticket_bundle(TGT_DOM, TGT_AUTH, cands[-1]["id"])
    if not t:
        continue
    tid = t["id"]

    scv, tcv = s["_conversations"], t["_conversations"]
    results = [
        check(dataset, case, sid, tid, "subject", s["subject"], t["subject"]),
        check(dataset, case, sid, tid, "status",
              STATUS.get(s["status"], s["status"]),
              STATUS.get(t["status"], t["status"])),
        check(dataset, case, sid, tid, "priority",
              PRIORITY.get(s["priority"]), PRIORITY.get(t["priority"])),
        check(dataset, case, sid, tid, "type", s.get("type"), t.get("type")),
        check(dataset, case, sid, tid, "source",
              SOURCE.get(s["source"], s["source"]),
              SOURCE.get(t["source"], t["source"])),
        check(dataset, case, sid, tid, "group",
              src_groups.get(s.get("group_id")), tgt_groups.get(t.get("group_id"))),
        check(dataset, case, sid, tid, "responder",
              src_agents.get(s.get("responder_id")),
              tgt_agents.get(t.get("responder_id"))),
        check(dataset, case, sid, tid, "requester",
              contact_email(SRC_DOM, SRC_AUTH, s.get("requester_id")),
              contact_email(TGT_DOM, TGT_AUTH, t.get("requester_id"))),
        check(dataset, case, sid, tid, "custom_fields",
              {k: v for k, v in (s.get("custom_fields") or {}).items() if v is not None},
              {k: v for k, v in (t.get("custom_fields") or {}).items() if v is not None}),
        check(dataset, case, sid, tid, "cc_emails",
              sorted(s.get("cc_emails") or []), sorted(t.get("cc_emails") or [])),
        check(dataset, case, sid, tid, "conversations", len(scv), len(tcv)),
        check(dataset, case, sid, tid, "private notes",
              sum(1 for c in scv if c.get("private")),
              sum(1 for c in tcv if c.get("private"))),
        check(dataset, case, sid, tid, "attachments",
              attachments(s), attachments(t)),
        check(dataset, case, sid, tid, "time entries",
              len(s["_time_entries"]), len(t["_time_entries"])),
        check(dataset, case, sid, tid, "source tags kept",
              True, set(s.get("tags") or []) <= set(t.get("tags") or [])),
    ]
    # informational: both HDM and our notes-mode flatten this, so it is
    # recorded but does not fail the run.
    si = sum(1 for c in scv if c.get("incoming"))
    ti = sum(1 for c in tcv if c.get("incoming"))
    rows.append({"dataset": dataset, "case": case, "source": sid, "target": tid,
                 "check": "incoming flag (informational)", "expected": si,
                 "actual": ti, "result": "PASS" if si == ti else "INFO"})

    bad = len([r for r in results if not r])
    flag = "OK  " if not bad else f"{bad} FAIL"
    print(f"  {case:<7} #{sid:<4} -> #{tid:<5} [{route:<7}] {flag}")

# ------------------------------------------------------------------ report
fails = [r for r in rows if r["result"] == "FAIL"]
info = [r for r in rows if r["result"] == "INFO"]
print("\n" + "=" * 78)
print(f"{len(rows)} checks   {len(rows) - len(fails) - len(info)} passed   "
      f"{len(fails)} failed   {len(info)} informational")
print("=" * 78)

if fails:
    print("\nFAILURES\n")
    for r in fails:
        print(f"  {r['case']:<7} #{r['source']} -> #{r['target']}  {r['check']}")
        print(f"        expected: {str(r['expected'])[:150]}")
        print(f"        actual:   {str(r['actual'])[:150]}")

if info:
    print(f"\n{len(info)} informational difference(s) - the `incoming` flag is "
          "flattened by\nnotes-mode migration AND by HDM, so it is reported "
          "but not counted as a failure.")

if CSV_OUT:
    with open(CSV_OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\nfull results -> {CSV_OUT}")

sys.exit(1 if fails else 0)
