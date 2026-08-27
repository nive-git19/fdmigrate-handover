"""Companies: auto-created on the target by name; deduped by name and via the
409 duplicate-value error body (which carries the existing company id)."""
from __future__ import annotations

import json

from ..client import FreshdeskApiError
from .base import Context, Heartbeat, norm, strip_empty, remap_cf

ENTITY = "company"


def _extract_existing_id(body: str) -> int | None:
    """A 409 duplicate error returns the conflicting record id in
    errors[].additional_info - often not findable via the list endpoint."""
    try:
        data = json.loads(body)
        for err in data.get("errors", []):
            info = err.get("additional_info") or {}
            for k in ("company_id", "id"):
                if info.get(k):
                    return int(info[k])
    except (ValueError, TypeError):
        pass
    return None


def run(ctx: Context) -> dict:
    ctx.logger.info("=" * 70)
    ctx.logger.info("PHASE: Companies")

    from .custom_fields import ensure_cf_map
    cf_map = ensure_cf_map(ctx, "company")
    # Prefetch target companies by name for dedup.
    target_by_name = {}
    for c in ctx.tgt.paginate("/companies"):
        target_by_name[norm(c.get("name"))] = c["id"]

    created = matched = skipped = failed = 0
    hb = Heartbeat(ctx.logger, "companies")
    for i, c in enumerate(ctx.src.paginate("/companies"), 1):
        sid = c["id"]
        if ctx.store.get_target(ENTITY, sid):
            skipped += 1
            continue

        name = c.get("name") or ""
        existing = target_by_name.get(norm(name))
        if existing:
            ctx.store.upsert(ENTITY, sid, existing, name=name)
            matched += 1
            continue

        payload = strip_empty({
            "name": name,
            "domains": c.get("domains") or [],
            "note": c.get("note"),
            "custom_fields": remap_cf(c.get("custom_fields"), cf_map),
        })
        resp = ctx.tgt.post("/companies", json=payload)
        if resp.status_code in (200, 201):
            new_id = resp.json()["id"]
            ctx.store.upsert(ENTITY, sid, new_id, name=name)
            target_by_name[norm(name)] = new_id
            created += 1
        elif resp.status_code == 409:
            existing = _extract_existing_id(resp.text)
            if existing:
                ctx.store.upsert(ENTITY, sid, existing, name=name)
                matched += 1
            else:
                ctx.store.upsert(ENTITY, sid, None, name=name, status="failed",
                                 error=f"409 no id: {resp.text[:200]}")
                failed += 1
        else:
            ctx.store.upsert(ENTITY, sid, None, name=name, status="failed",
                             error=f"HTTP {resp.status_code}: {resp.text[:200]}")
            ctx.log(ENTITY, sid, "ERROR", "create_failed", f"{resp.status_code} {resp.text[:200]}")
            failed += 1

        hb.beat(i, created=created, matched=matched, skipped=skipped, failed=failed)

    ctx.logger.info(f"Companies: created={created} matched={matched} "
                    f"skipped={skipped} failed={failed}")
    return {"created": created, "matched": matched, "skipped": skipped, "failed": failed}
