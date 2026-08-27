"""Seed N (default 1000) varied test tickets into SOURCE for bulk testing.
1 private note each, NO attachments. Variety: all 4 statuses/priorities/types,
~half assigned to agent2 + grouped, every 5th has unicode/special chars in the
subject, every 7th has a CC. Tagged 'zzz-bulk-1k'.
"""
import os
import sys
from fdmigrate.config import load_config
from fdmigrate.logs import setup_logging
from fdmigrate.runner import build_clients

N = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
TAG = "zzz-bulk-1k"

cfg = load_config("config.yaml")
log = setup_logging("logs/_gen1k.log")
src, _ = build_clients(cfg, log)

agents = list(src.paginate("/agents"))
def by_email(e):
    for a in agents:
        if ((a.get("contact") or {}).get("email") or "").lower() == e.lower():
            return a["id"]
    return None
# Which agent to assign the "assigned" half of the tickets to.
# Set FD_SEED_AGENT_EMAIL to pick a specific one; otherwise fall back to the
# second agent on the account (or the only one, on a fresh trial).
_want = os.environ.get("FD_SEED_AGENT_EMAIL", "").strip()
agent2 = by_email(_want) if _want else None
if agent2 is None:
    if _want:
        print(f"warning: no agent matches {_want}; falling back to account order")
    agent2 = agents[1]["id"] if len(agents) > 1 else (agents[0]["id"] if agents else None)
if agent2 is None:
    raise SystemExit("no agents on the source account - create one before seeding")
contacts = list(src.paginate("/contacts"))
groups = list(src.paginate("/groups"))
gid = groups[0]["id"]
print(f"agent2={agent2} | contacts={len(contacts)} | group={gid}")
if not contacts:
    raise SystemExit("no source contacts to use as requesters")

STAT = [2, 3, 4, 5]; PRI = [1, 2, 3, 4]; SRC = [1, 2, 3]
TYP = ["Question", "Incident", "Problem", "Feature Request"]

made = notes = 0
for i in range(N):
    req = contacts[i % len(contacts)]
    st, pr, so, ty = STAT[i % 4], PRI[i % 4], SRC[i % 3], TYP[i % 4]
    assigned = (i % 2 == 0)
    subj = f"[1K {i+1}] {ty} {st}/{pr}"
    if i % 5 == 0:
        subj += " - cafe/naive #unicode & <b>tag</b>"
    payload = {
        "subject": subj,
        "description": f"<p>Bulk-1k ticket {i+1}. Request from {req.get('name')}.</p>",
        "requester_id": req["id"], "status": st, "priority": pr, "source": so, "type": ty,
        "group_id": gid, "tags": [TAG, f"status{st}", f"prio{pr}"],
    }
    if assigned and agent2:
        payload["responder_id"] = agent2
    if i % 7 == 0:
        payload["cc_emails"] = ["cc-watcher@example.com"]
    r = src.post("/tickets", json=payload)
    if r.status_code not in (200, 201):
        print(f"  ticket {i+1} FAILED {r.status_code}: {r.text[:120]}")
        continue
    tid = r.json()["id"]; made += 1
    uid = agent2 if assigned else req["id"]
    if uid:
        nr = src.post(f"/tickets/{tid}/notes",
                      json={"body": f"<p>Note on ticket {i+1}.</p>", "private": True, "user_id": uid})
        if nr.status_code in (200, 201):
            notes += 1
    if made % 50 == 0:
        print(f"  ...{made} tickets")

print(f"DONE: {made} tickets, {notes} notes (tag '{TAG}')")
