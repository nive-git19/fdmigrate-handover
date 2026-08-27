"""Agents: matched to the target by email ONLY. Never created, no default
fallback, no many-to-one (per the project's confirmed decision). Unmatched
source agents are logged so tickets they own migrate unassigned, by design."""
from __future__ import annotations

from .base import Context, norm

ENTITY = "agent"


def _agent_email(a: dict) -> str:
    return norm((a.get("contact") or {}).get("email") or a.get("email"))


def run(ctx: Context) -> dict:
    ctx.logger.info("=" * 70)
    ctx.logger.info("PHASE: Agents (match-only by email)")

    # Build target email -> id index.
    target_by_email = {}
    for a in ctx.tgt.paginate("/agents"):
        email = _agent_email(a)
        if email:
            target_by_email[email] = a["id"]

    matched = unmatched = skipped = 0
    for a in ctx.src.paginate("/agents"):
        sid = a["id"]
        src_email = _agent_email(a)
        name = (a.get("contact") or {}).get("name") or src_email

        if ctx.store.get_target(ENTITY, sid):
            skipped += 1
            continue

        # Manual override: source email -> target email, then resolve target id.
        want_email = ctx.cfg.map_agents.get(src_email, src_email)
        tgt_id = target_by_email.get(want_email)

        if tgt_id:
            ctx.store.upsert(ENTITY, sid, tgt_id, name=name, status="done")
            via = "manual map" if want_email != src_email else "email match"
            ctx.log(ENTITY, sid, "INFO", "matched", f"{src_email} -> target {tgt_id} ({via})")
            matched += 1
        else:
            ctx.store.upsert(ENTITY, sid, None, name=name, status="failed",
                             error=f"no target agent for {want_email}")
            ctx.log(ENTITY, sid, "WARNING", "unmatched",
                    f"{src_email}: no matching target agent - tickets will migrate unassigned")
            unmatched += 1

    ctx.logger.info(f"Agents: matched={matched} unmatched={unmatched} skipped={skipped}")
    return {"matched": matched, "unmatched": unmatched, "skipped": skipped}
