"""Groups: auto-created on the target by name, deduped by name. Membership is
synced from the source group's DETAIL endpoint (the list endpoint returns
agent_ids=None) through the agent idmap, so ticket assignment isn't dropped."""
from __future__ import annotations

from ..client import FreshdeskApiError
from .base import Context, norm, strip_empty

ENTITY = "group"


def run(ctx: Context) -> dict:
    ctx.logger.info("=" * 70)
    ctx.logger.info("PHASE: Groups")

    target_by_name = {}
    for g in ctx.tgt.paginate("/groups"):
        target_by_name[norm(g.get("name"))] = g["id"]

    agent_map = ctx.store.target_map("agent")

    created = matched = skipped = failed = 0
    for g in ctx.src.paginate("/groups"):
        sid = g["id"]
        name = g.get("name") or ""

        target_id = ctx.store.get_target(ENTITY, sid) or target_by_name.get(norm(name))

        if not target_id:
            payload = strip_empty({"name": name, "description": g.get("description")})
            resp = ctx.tgt.post("/groups", json=payload)
            if resp.status_code in (200, 201):
                target_id = resp.json()["id"]
                target_by_name[norm(name)] = target_id
                created += 1
            else:
                ctx.store.upsert(ENTITY, sid, None, name=name, status="failed",
                                 error=f"HTTP {resp.status_code}: {resp.text[:200]}")
                ctx.log(ENTITY, sid, "ERROR", "create_failed", f"{resp.status_code} {resp.text[:200]}")
                failed += 1
                continue
        else:
            if ctx.store.get_target(ENTITY, sid):
                skipped += 1
            else:
                matched += 1

        ctx.store.upsert(ENTITY, sid, target_id, name=name)

        # Sync membership via the DETAIL endpoint (list returns agent_ids=None).
        try:
            detail = ctx.src.get(f"/groups/{sid}")
            src_agent_ids = detail.get("agent_ids") or []
        except FreshdeskApiError:
            src_agent_ids = []
        target_agents = [int(agent_map[str(aid)]) for aid in src_agent_ids
                         if str(aid) in agent_map]
        if target_agents:
            # MERGE, don't replace. A PUT with agent_ids overwrites the group's
            # whole membership, so if this group already existed on the target
            # (matched by name) a bare replace would silently DROP agents the
            # client already had in it. Union the source-mapped agents with
            # whoever is already there, and only write when it actually changes.
            try:
                current = ctx.tgt.get(f"/groups/{target_id}").get("agent_ids") or []
            except FreshdeskApiError:
                current = []
            current_ids = {int(x) for x in current}
            merged = sorted(current_ids | set(target_agents))
            if set(merged) != current_ids:
                upd = ctx.tgt.put(f"/groups/{target_id}", json={"agent_ids": merged})
                if upd.status_code not in (200, 201):
                    ctx.log(ENTITY, sid, "WARNING", "membership_failed",
                            f"{upd.status_code} {upd.text[:150]}")

    ctx.logger.info(f"Groups: created={created} matched={matched} "
                    f"skipped={skipped} failed={failed}")
    return {"created": created, "matched": matched, "skipped": skipped, "failed": failed}
