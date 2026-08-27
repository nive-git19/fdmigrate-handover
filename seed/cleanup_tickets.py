"""Clean slate for bulk testing: delete ALL tickets, then purge the local
ticket/conversation state so a fresh migration isn't silently skipped.

    python seed/cleanup_tickets.py                    # preview only, deletes nothing
    python seed/cleanup_tickets.py --yes              # delete TARGET tickets
    python seed/cleanup_tickets.py --yes --include-source   # also delete SOURCE tickets

Tickets ONLY - agents/contacts/groups/companies/canned/KB are left untouched.
Soft-delete (Freshdesk moves them to Trash). Every id is logged.

!!! THROWAWAY TEST ACCOUNTS ONLY !!!
This is irreversible at scale and it does NOT check whether an account is a
trial. Deleting SOURCE tickets destroys the very data you are migrating, which
is why it now requires an explicit second flag. Read your config.yaml and be
certain which two accounts it points at before passing --yes.
"""
import sqlite3
import sys

from fdmigrate.config import load_config
from fdmigrate.logs import setup_logging
from fdmigrate.runner import build_clients

WIDE = "2008-01-01T00:00:00Z"

CONFIRM = "--yes" in sys.argv
INCLUDE_SOURCE = "--include-source" in sys.argv

cfg = load_config("config.yaml")
log = setup_logging("logs/_cleanup.log")
src, tgt = build_clients(cfg, log)

print(f"  source : {cfg.source_domain}")
print(f"  target : {cfg.target_domain}")
if not CONFIRM:
    print("\nPREVIEW ONLY - nothing will be deleted. Re-run with --yes to delete.")
elif INCLUDE_SOURCE:
    print("\n*** --include-source given: SOURCE tickets WILL BE DELETED TOO. ***")


def delete_all_tickets(client, label):
    ids = [t["id"] for t in client.paginate(
        "/tickets", params={"updated_since": WIDE, "order_by": "updated_at", "order_type": "asc"})]
    if not CONFIRM:
        print(f"[{label}] {len(ids)} tickets found -> would delete (preview only)")
        return 0
    print(f"[{label}] {len(ids)} tickets found -> deleting (tickets only)")
    ok = 0
    for tid in ids:
        r = client.delete(f"/tickets/{tid}")
        if r.status_code in (200, 204):
            ok += 1
        else:
            print(f"  [{label}] delete {tid} -> {r.status_code} {r.text[:100]}")
    print(f"[{label}] deleted {ok}/{len(ids)}")
    # confirm
    left = sum(1 for _ in client.paginate("/tickets", params={"updated_since": WIDE}))
    print(f"[{label}] tickets remaining now: {left}")
    return ok


if INCLUDE_SOURCE:
    print("=== SOURCE ===")
    delete_all_tickets(src, "source")
else:
    print("=== SOURCE === skipped (pass --include-source to delete source tickets too)")

print("=== TARGET ===")
delete_all_tickets(tgt, "target")

if not CONFIRM:
    print("\nPreview complete. Nothing was deleted. Re-run with --yes to proceed.")
    raise SystemExit(0)

print("=== purge local ticket state (keep agent/company/contact/group/canned/kb) ===")
db = sqlite3.connect("migration.db")
db.execute("DELETE FROM idmap WHERE entity='ticket'")
db.execute("DELETE FROM attachments WHERE entity IN ('ticket','conversation')")
db.execute("DELETE FROM conversations")
db.commit()
remaining = db.execute("SELECT entity, COUNT(*) FROM idmap GROUP BY entity").fetchall()
db.close()
print("local idmap after purge:", remaining)
print("CLEANUP DONE.")
