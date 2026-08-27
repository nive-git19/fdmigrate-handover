"""Contacts: created on the target, or - on an email match - UPDATED from source
(the project's confirmed update-on-match decision, matching commercial tools).
Deduped by email, by the company idmap, and via the 409 error body."""
from __future__ import annotations

import json

from .base import Context, Heartbeat, norm, strip_empty, remap_cf

ENTITY = "contact"


def _extract_existing_id(body: str) -> int | None:
    try:
        data = json.loads(body)
        for err in data.get("errors", []):
            info = err.get("additional_info") or {}
            for k in ("user_id", "id"):
                if info.get(k):
                    return int(info[k])
    except (ValueError, TypeError):
        pass
    return None


def _payload(c: dict, company_map: dict, cf_map: dict) -> dict:
    body = {
        "name": c.get("name") or "(no name)",
        "email": c.get("email"),
        "phone": c.get("phone"),
        "mobile": c.get("mobile"),
        "job_title": c.get("job_title"),
        "address": c.get("address"),
        "twitter_id": c.get("twitter_id"),
        "unique_external_id": c.get("unique_external_id"),
        "other_emails": c.get("other_emails") or [],
        "tags": c.get("tags") or [],
        "time_zone": c.get("time_zone"),
        "language": c.get("language"),
        "custom_fields": remap_cf(c.get("custom_fields"), cf_map),
    }
    src_company = c.get("company_id")
    if src_company and company_map.get(str(src_company)):
        body["company_id"] = int(company_map[str(src_company)])
    return strip_empty(body)


def _lookup_by_email(ctx: Context, email: str) -> int | None:
    try:
        results = ctx.tgt.get("/contacts", params={"email": email})
        if results:
            return results[0]["id"]
    except Exception:
        pass
    return None


def run(ctx: Context) -> dict:
    ctx.logger.info("=" * 70)
    ctx.logger.info("PHASE: Contacts")

    from .custom_fields import ensure_cf_map
    company_map = ctx.store.target_map("company")
    cf_map = ensure_cf_map(ctx, "contact")
    update_on_match = ctx.cfg.contacts_update_on_match

    created = updated = matched = skipped = failed = 0
    hb = Heartbeat(ctx.logger, "contacts")
    # Windowed iterator: transparently pages past Freshdesk's 30,000-record
    # list cap (a plain paginate() silently truncates a 200k-contact account).
    for i, c in enumerate(ctx.src.iter_contacts(ctx.cfg.contacts_updated_since), 1):
        sid = c["id"]
        if ctx.store.get_target(ENTITY, sid):
            skipped += 1
            continue

        email = c.get("email")
        name = c.get("name") or email or str(sid)
        payload = _payload(c, company_map, cf_map)

        if not email:
            # Freshdesk allows phone-only contacts; create attempt still valid.
            ctx.log(ENTITY, sid, "WARNING", "no_email", "contact has no email")

        resp = ctx.tgt.post("/contacts", json=payload)
        if resp.status_code in (200, 201):
            new_id = resp.json()["id"]
            ctx.store.upsert(ENTITY, sid, new_id, name=name)
            created += 1
        elif resp.status_code == 409:
            existing = _extract_existing_id(resp.text)
            if not existing and email:
                existing = _lookup_by_email(ctx, email)
            if existing:
                if update_on_match:
                    upd = ctx.tgt.put(f"/contacts/{existing}", json=payload)
                    if upd.status_code in (200, 201):
                        ctx.store.upsert(ENTITY, sid, existing, name=name)
                        updated += 1
                    else:
                        # Update failed but the match is valid - record the map, log the update miss.
                        ctx.store.upsert(ENTITY, sid, existing, name=name)
                        ctx.log(ENTITY, sid, "WARNING", "update_failed",
                                f"{upd.status_code} {upd.text[:200]}")
                        matched += 1
                else:
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

        hb.beat(i, created=created, updated=updated, matched=matched,
                skipped=skipped, failed=failed)

    ctx.logger.info(f"Contacts: created={created} updated={updated} matched={matched} "
                    f"skipped={skipped} failed={failed}")
    return {"created": created, "updated": updated, "matched": matched,
            "skipped": skipped, "failed": failed}
