"""Knowledge base: categories -> folders -> nested subfolders (recursive) ->
articles. Idempotent: categories/folders matched by name, articles by title,
within their parent. Article author mapping and oversize attachments are logged."""
from __future__ import annotations

from ..client import FreshdeskApiError
from .base import Context, norm, strip_empty

CAT, FOLDER, ART = "kb_category", "kb_folder", "kb_article"


def _index_by_name(items, key="name"):
    return {norm(i.get(key)): i["id"] for i in items}


def _find_existing_folder(ctx: Context, tgt_cat_id, parent_tgt_folder_id, name):
    """Look up an already-existing target folder by name, under the parent
    subfolder if nested, else under the category. Mirrors the company/group
    name-dedup so a folder that already exists is mapped, not re-created (409)."""
    path = (f"/solutions/folders/{parent_tgt_folder_id}/subfolders"
            if parent_tgt_folder_id
            else f"/solutions/categories/{tgt_cat_id}/folders")
    try:
        return _index_by_name(ctx.tgt.paginate(path)).get(norm(name))
    except FreshdeskApiError:
        return None


def _migrate_articles(ctx: Context, src_folder_id, tgt_folder_id, agent_map) -> dict:
    counts = {"created": 0, "matched": 0, "failed": 0}
    try:
        tgt_articles = _index_by_name(ctx.tgt.paginate(f"/solutions/folders/{tgt_folder_id}/articles"),
                                      key="title")
    except FreshdeskApiError:
        tgt_articles = {}

    for a in ctx.src.paginate(f"/solutions/folders/{src_folder_id}/articles"):
        sid = a["id"]
        title = a.get("title") or ""
        if ctx.store.get_target(ART, sid):
            counts["matched"] += 1
            continue
        existing = tgt_articles.get(norm(title))
        if existing:
            ctx.store.upsert(ART, sid, existing, name=title)
            counts["matched"] += 1
            continue
        payload = strip_empty({
            "title": title,
            "description": a.get("description") or " ",
            "status": a.get("status") or 1,   # 1=draft, 2=published
            "type": a.get("type"),
            "tags": a.get("tags") or [],
            "seo_data": a.get("seo_data"),
        })
        resp = ctx.tgt.post(f"/solutions/folders/{tgt_folder_id}/articles", json=payload)
        if resp.status_code in (200, 201):
            ctx.store.upsert(ART, sid, resp.json()["id"], name=title)
            counts["created"] += 1
        else:
            ctx.store.upsert(ART, sid, None, name=title, status="failed",
                             error=f"HTTP {resp.status_code}: {resp.text[:200]}")
            ctx.log(ART, sid, "ERROR", "create_failed", f"{resp.status_code} {resp.text[:200]}")
            counts["failed"] += 1
    return counts


def _migrate_folder_tree(ctx: Context, src_folder, tgt_cat_id, agent_map,
                         parent_tgt_folder_id=None, company_map=None) -> dict:
    """Create one folder (under category or as a subfolder of parent), migrate its
    articles, then recurse into subfolders."""
    totals = {"folders": 0, "articles_created": 0, "articles_matched": 0, "articles_failed": 0}
    sid = src_folder["id"]
    name = src_folder.get("name") or ""

    # Layer 1: already mapped in this job. Layer 2: exists in target by name.
    tgt_folder_id = (ctx.store.get_target(FOLDER, sid)
                     or _find_existing_folder(ctx, tgt_cat_id, parent_tgt_folder_id, name))
    if tgt_folder_id:
        ctx.store.upsert(FOLDER, sid, tgt_folder_id, name=name)
    else:
        # Company-restricted folders (visibility=4) carry company_ids. These are
        # SOURCE ids and are meaningless on the target - remap them through the
        # company idmap so the folder is restricted to the CORRECT companies (or
        # drop the ones we couldn't map rather than sending a bad id).
        cmap = company_map or {}
        tgt_company_ids = [int(cmap[str(cid)]) for cid in (src_folder.get("company_ids") or [])
                           if str(cid) in cmap]
        payload = strip_empty({
            "name": name,
            "description": src_folder.get("description"),
            "visibility": src_folder.get("visibility") or 1,
            "company_ids": tgt_company_ids,
        })
        if parent_tgt_folder_id:
            payload["parent_folder_id"] = int(parent_tgt_folder_id)
        # Subfolders are created under the parent's category endpoint with a parent ref.
        path = f"/solutions/categories/{tgt_cat_id}/folders"
        resp = ctx.tgt.post(path, json=payload)
        if resp.status_code in (200, 201):
            tgt_folder_id = resp.json()["id"]
            ctx.store.upsert(FOLDER, sid, tgt_folder_id, name=name)
            totals["folders"] += 1
        elif resp.status_code == 409:
            # Layer 3: lost a race / pre-existing under a different listing - re-resolve by name.
            tgt_folder_id = _find_existing_folder(ctx, tgt_cat_id, parent_tgt_folder_id, name)
            if tgt_folder_id:
                ctx.store.upsert(FOLDER, sid, tgt_folder_id, name=name)
                ctx.log(FOLDER, sid, "INFO", "matched", f"existing target folder {tgt_folder_id} (409 dedup)")
            else:
                ctx.store.upsert(FOLDER, sid, None, name=name, status="failed",
                                 error=f"HTTP 409 but no matching folder found: {resp.text[:150]}")
                ctx.log(FOLDER, sid, "ERROR", "create_failed", f"409 unresolved: {resp.text[:200]}")
                return totals
        else:
            ctx.store.upsert(FOLDER, sid, None, name=name, status="failed",
                             error=f"HTTP {resp.status_code}: {resp.text[:200]}")
            ctx.log(FOLDER, sid, "ERROR", "create_failed", f"{resp.status_code} {resp.text[:200]}")
            return totals

    art = _migrate_articles(ctx, sid, tgt_folder_id, agent_map)
    totals["articles_created"] += art["created"]
    totals["articles_matched"] += art["matched"]
    totals["articles_failed"] += art["failed"]

    # Recurse into nested subfolders, if the plan supports them.
    try:
        subfolders = list(ctx.src.paginate(f"/solutions/folders/{sid}/subfolders"))
    except FreshdeskApiError:
        subfolders = []
    for sub in subfolders:
        sub_totals = _migrate_folder_tree(ctx, sub, tgt_cat_id, agent_map, tgt_folder_id,
                                          company_map=company_map)
        for k in totals:
            totals[k] += sub_totals[k]
    return totals


def run(ctx: Context) -> dict:
    ctx.logger.info("=" * 70)
    ctx.logger.info("PHASE: Knowledge base")

    agent_map = ctx.store.target_map("agent")
    company_map = ctx.store.target_map("company")
    try:
        tgt_categories = _index_by_name(ctx.tgt.paginate("/solutions/categories"))
    except FreshdeskApiError as e:
        ctx.logger.warning(f"KB: cannot list target categories: {e} - skipping phase.")
        return {"skipped_phase": True}

    cats = folders = articles = 0
    for cat in ctx.src.paginate("/solutions/categories"):
        sid = cat["id"]
        name = cat.get("name") or ""
        tgt_cat_id = ctx.store.get_target(CAT, sid) or tgt_categories.get(norm(name))
        if not tgt_cat_id:
            resp = ctx.tgt.post("/solutions/categories",
                                json=strip_empty({"name": name, "description": cat.get("description")}))
            if resp.status_code in (200, 201):
                tgt_cat_id = resp.json()["id"]
                cats += 1
            else:
                ctx.store.upsert(CAT, sid, None, name=name, status="failed",
                                 error=f"HTTP {resp.status_code}: {resp.text[:200]}")
                ctx.log(CAT, sid, "ERROR", "create_failed", f"{resp.status_code} {resp.text[:200]}")
                continue
        ctx.store.upsert(CAT, sid, tgt_cat_id, name=name)

        for folder in ctx.src.paginate(f"/solutions/categories/{sid}/folders"):
            t = _migrate_folder_tree(ctx, folder, tgt_cat_id, agent_map, company_map=company_map)
            folders += t["folders"]
            articles += t["articles_created"]

    ctx.logger.info(f"Knowledge base: categories+={cats} folders+={folders} articles+={articles}")
    return {"categories": cats, "folders": folders, "articles": articles}
