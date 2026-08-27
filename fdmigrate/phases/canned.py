"""Canned responses: migrated FLAT (no folder structure - custom canned-response
folders are non-functional on standard plans). Deduped by title. group_ids are
remapped through the group idmap with a visibility=1 (all-agents) fallback."""
from __future__ import annotations

from ..client import FreshdeskApiError
from .base import Context, norm, strip_empty

ENTITY = "canned_response"


def _default_target_folder(ctx: Context) -> int | None:
    try:
        folders = ctx.tgt.get("/canned_response_folders") or []
    except FreshdeskApiError as e:
        ctx.logger.warning(f"Canned: cannot list target folders: {e}")
        return None
    if not folders:
        return None
    # Prefer a folder named 'Personal', else the first.
    for f in folders:
        if norm(f.get("name")) == "personal":
            return f["id"]
    return folders[0]["id"]


def _iter_source_responses(ctx: Context):
    """Yield full source canned-response detail dicts across all folders."""
    try:
        folders = ctx.src.get("/canned_response_folders") or []
    except FreshdeskApiError as e:
        ctx.logger.warning(f"Canned: cannot list source folders: {e}")
        return
    for f in folders:
        try:
            detail = ctx.src.get(f"/canned_response_folders/{f['id']}")
        except FreshdeskApiError:
            continue
        for stub in detail.get("canned_responses", []) or []:
            try:
                yield ctx.src.get(f"/canned_responses/{stub['id']}")
            except FreshdeskApiError as e:
                ctx.log(ENTITY, stub.get("id"), "WARNING", "fetch_failed", str(e))


def run(ctx: Context) -> dict:
    ctx.logger.info("=" * 70)
    ctx.logger.info("PHASE: Canned responses (flat)")

    folder_id = _default_target_folder(ctx)
    if not folder_id:
        ctx.logger.warning("Canned: no target folder available - skipping phase.")
        return {"skipped_phase": True}

    # Prefetch existing target titles for dedup.
    existing_titles = set()
    try:
        for f in ctx.tgt.get("/canned_response_folders") or []:
            d = ctx.tgt.get(f"/canned_response_folders/{f['id']}")
            for cr in d.get("canned_responses", []) or []:
                existing_titles.add(norm(cr.get("title")))
    except FreshdeskApiError:
        pass

    group_map = ctx.store.target_map("group")
    created = matched = skipped = failed = 0

    for cr in _iter_source_responses(ctx):
        sid = cr["id"]
        title = cr.get("title") or ""
        if ctx.store.get_target(ENTITY, sid):
            skipped += 1
            continue
        if norm(title) in existing_titles:
            ctx.store.upsert(ENTITY, sid, None, name=title)  # title-matched, no id needed
            matched += 1
            continue

        group_ids = [int(group_map[str(g)]) for g in (cr.get("group_ids") or [])
                     if str(g) in group_map]
        payload = strip_empty({
            "title": title,
            "content_html": cr.get("content_html") or cr.get("content"),
            "folder_id": folder_id,
            "visibility": cr.get("visibility") or 1,
            "group_ids": group_ids,
        })
        resp = ctx.tgt.post("/canned_responses", json=payload)
        if resp.status_code in (200, 201):
            ctx.store.upsert(ENTITY, sid, resp.json().get("id"), name=title)
            existing_titles.add(norm(title))
            created += 1
        else:
            ctx.store.upsert(ENTITY, sid, None, name=title, status="failed",
                             error=f"HTTP {resp.status_code}: {resp.text[:200]}")
            ctx.log(ENTITY, sid, "ERROR", "create_failed", f"{resp.status_code} {resp.text[:200]}")
            failed += 1

    ctx.logger.info(f"Canned responses: created={created} matched={matched} "
                    f"skipped={skipped} failed={failed}")
    return {"created": created, "matched": matched, "skipped": skipped, "failed": failed}
