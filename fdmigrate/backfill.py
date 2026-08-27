"""Backfill responder (agent) + group onto tickets that were already migrated
while the agent/group idmap was incomplete.

Why this exists: the tickets phase marks a ticket 'done' and never re-touches it.
So if a ticket was created back when its source agent hadn't been mapped (e.g. an
email mismatch later fixed via mapping.agents), the target ticket stays unassigned
forever. This pass re-resolves responder_id/group_id from the now-complete idmaps
and PUTs them onto the existing target ticket - only when they're actually missing
or wrong, so it's safe to re-run and cheap when there's nothing to fix.

Run it AFTER re-running the agents (and groups) phase so the idmaps are populated:
    python -m fdmigrate fix-assignments --config config.yaml
"""
from __future__ import annotations

from .client import FreshdeskApiError
from .phases.base import Context

ENTITY = "ticket"


def run(ctx: Context) -> dict:
    ctx.logger.info("=" * 70)
    ctx.logger.info("BACKFILL: ticket responder + group assignment")

    agent_map = ctx.store.target_map("agent")
    group_map = ctx.store.target_map("group")
    if not agent_map and not group_map:
        ctx.logger.warning("agent and group idmaps are both empty - run the "
                           "agents/groups phases first. Nothing to backfill.")
        return {"updated": 0, "already_ok": 0, "no_mapping": 0, "failed": 0}

    rows = ctx.store.records(ENTITY, statuses=("done", "partial"))
    ctx.logger.info(f"Examining {len(rows)} migrated ticket(s)...")

    updated = already_ok = no_mapping = failed = 0

    for row in rows:
        sid = row["source_id"]
        tid = row["target_id"]
        try:
            src_t = ctx.src.get(f"/tickets/{sid}")
        except FreshdeskApiError as e:
            ctx.log(ENTITY, sid, "WARNING", "src_fetch_failed",
                    f"could not read source ticket: {e}")
            failed += 1
            continue

        # Resolve desired target values from the idmaps.
        desired = {}
        src_responder = src_t.get("responder_id")
        if src_responder and agent_map.get(str(src_responder)):
            desired["responder_id"] = int(agent_map[str(src_responder)])
        src_group = src_t.get("group_id")
        if src_group and group_map.get(str(src_group)):
            desired["group_id"] = int(group_map[str(src_group)])

        if not desired:
            # Source ticket had no assignee/group, or it's still unmapped.
            no_mapping += 1
            continue

        # Only PUT what's actually missing/different on the target.
        try:
            tgt_t = ctx.tgt.get(f"/tickets/{tid}")
        except FreshdeskApiError as e:
            ctx.log(ENTITY, sid, "WARNING", "tgt_fetch_failed",
                    f"could not read target ticket {tid}: {e}")
            failed += 1
            continue

        patch = {k: v for k, v in desired.items() if tgt_t.get(k) != v}
        if not patch:
            already_ok += 1
            continue

        resp = ctx.tgt.put(f"/tickets/{tid}", json=patch)
        if resp.status_code in (200, 201):
            ctx.log(ENTITY, sid, "INFO", "assignment_backfilled",
                    f"target {tid} <- {patch}")
            updated += 1
        else:
            ctx.log(ENTITY, sid, "ERROR", "backfill_failed",
                    f"target {tid}: {resp.status_code} {resp.text[:150]}")
            failed += 1

    ctx.logger.info(f"Backfill: updated={updated} already_ok={already_ok} "
                    f"no_mapping={no_mapping} failed={failed}")
    return {"updated": updated, "already_ok": already_ok,
            "no_mapping": no_mapping, "failed": failed}
