"""Tickets + conversations + attachments - the high-volume phase.

Reliability:
  - Date-windowed fetch (FreshdeskClient.iter_tickets) gets past the 30k list cap.
  - A ticket is recorded 'partial' the instant it's created (with its target id),
    then 'done' once all conversations are replayed - so a crash mid-ticket never
    creates a duplicate and resumes only the unfinished conversations.
  - Attachments stream to disk; oversize/unavailable ones go to the manifest,
    never silently lost.
Fidelity:
  - status/priority/source/type/tags/cc_emails/due dates/custom fields preserved.
  - Conversations replicate AS-IS by default: customer replies stay incoming
    messages, agent replies post as real replies, private notes stay private.
    Requires notifications/automations OFF on the target (see MIGRATION_GUIDE).
  - No content is injected: a migrated ticket carries only its marker tag.
    (provenance_banner: true restores the old timestamp banner; the API rejects
    back-dating, so created_date_custom_field is how original dates survive.)
  - responder/group/requester resolved through the agent/group/contact idmaps.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from ..client import ATTACHMENT_TIMEOUT, FreshdeskApiError
from .base import (Context, Heartbeat, note_provenance_line, provenance_banner,
                   strip_empty)

ENTITY = "ticket"


def _restore_status(ctx, tgt_id, mapped_status, source_id=None) -> bool:
    """Replaying conversations can REOPEN a resolved/closed ticket (Freshdesk's
    'reopen on response' rule). Re-apply the intended status afterwards so the
    migrated ticket keeps its correct final status (4=Resolved, 5=Closed).

    Returns True if the final status is confirmed. A failure here used to be
    swallowed silently, so a ticket the target refused to close (e.g. a
    required-for-closure custom field is empty) would land Open with no trace.
    Now every failure is logged so it shows up in the events/failures report."""
    if mapped_status not in (4, 5):
        return True
    try:
        resp = ctx.tgt.put(f"/tickets/{tgt_id}", json={"status": mapped_status})
    except Exception as e:
        ctx.log(ENTITY, source_id or tgt_id, "WARNING", "status_restore_error",
                f"could not re-apply final status {mapped_status} to target {tgt_id}: {e}")
        return False
    if resp.status_code not in (200, 201):
        ctx.log(ENTITY, source_id or tgt_id, "WARNING", "status_restore_failed",
                f"target {tgt_id} did not accept final status {mapped_status} "
                f"(often a required-for-closure field is empty): "
                f"{resp.status_code} {resp.text[:150]}")
        return False
    return True


def _is_future(ts: str | None) -> bool:
    """True if an ISO timestamp is still in the future. Migrated tickets are created
    'now' (we can't back-date), so original past due-dates are invalid and dropped."""
    if not ts:
        return False
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")) > datetime.now(timezone.utc)
    except ValueError:
        return False


# Tags this tool recognizes as "already migrated" markers. The first is this
# tool's own scheme; the second is the legacy V1 Odoo tool's, so we never
# duplicate tickets it already migrated.
MARKER_PREFIXES = ("fd-migration-", "fdmig-src-")


def _marker_tag(source_id) -> str:
    """Hidden tag stamped on every migrated ticket so it can be recognized later
    and never duplicated. The trailing number is the original source ticket id."""
    return f"fd-migration-{source_id}"


def _prefetch_target_markers(ctx) -> dict:
    """Scan the target's tickets for migration marker tags and map the original
    SOURCE ticket id -> existing target id. Parses the trailing id, so it matches
    both this tool's `fd-migration-<id>` and the legacy `fdmig-src-<conn>-<id>`
    tags. Uses the date-windowed iterator, so it scales past the 30k list cap."""
    markers = {}
    try:
        for t in ctx.tgt.iter_tickets(ctx.cfg.tickets_updated_since, include_spam=False):
            for tag in t.get("tags") or []:
                if tag.startswith(MARKER_PREFIXES):
                    sid = tag.rsplit("-", 1)[-1]
                    if sid.isdigit():
                        markers[sid] = t["id"]
    except FreshdeskApiError as e:
        ctx.log(ENTITY, 0, "WARNING", "marker_prefetch_failed",
                f"could not prefetch target markers ({e}); relying on local idmap only")
    return markers


# ----------------------------------------------------------- match-wise filter
def _as_list(v):
    return v if isinstance(v, (list, tuple)) else [v]


def _matches_filters(t: dict, f: dict) -> bool:
    """True if the ticket matches ALL configured filters. An absent/empty filter
    key is no constraint. Supports status/priority/group/responder (ids),
    type/tags (strings), and created/updated date bounds (ISO strings)."""
    if not f:
        return True
    if f.get("status") and t.get("status") not in _as_list(f["status"]):
        return False
    if f.get("priority") and t.get("priority") not in _as_list(f["priority"]):
        return False
    if f.get("group_id") and t.get("group_id") not in _as_list(f["group_id"]):
        return False
    if f.get("responder_id") and t.get("responder_id") not in _as_list(f["responder_id"]):
        return False
    if f.get("type") and t.get("type") not in _as_list(f["type"]):
        return False
    if f.get("requester_id") and t.get("requester_id") not in _as_list(f["requester_id"]):
        return False
    if f.get("company_id") and t.get("company_id") not in _as_list(f["company_id"]):
        return False
    if f.get("tags"):
        wanted = {str(x).lower() for x in _as_list(f["tags"])}
        have = {str(x).lower() for x in (t.get("tags") or [])}
        if not (wanted & have):      # ticket must carry at least one wanted tag
            return False
    created = t.get("created_at") or ""
    if f.get("created_after") and created < str(f["created_after"]):
        return False
    if f.get("created_before") and created > str(f["created_before"]):
        return False
    updated = t.get("updated_at") or ""
    if f.get("updated_after") and updated < str(f["updated_after"]):
        return False
    if f.get("updated_before") and updated > str(f["updated_before"]):
        return False
    return True


# ----------------------------------------------------------------- helpers
def _flatten_form(payload: dict) -> list[tuple[str, str]]:
    """Flatten a payload into multipart form fields with Freshdesk's bracket
    notation for arrays and custom_fields."""
    form: list[tuple[str, str]] = []
    for k, v in payload.items():
        if k == "custom_fields" and isinstance(v, dict):
            for ck, cv in v.items():
                form.append((f"custom_fields[{ck}]", "" if cv is None else str(cv)))
        elif isinstance(v, list):
            for item in v:
                form.append((f"{k}[]", str(item)))
        elif isinstance(v, bool):
            form.append((k, "true" if v else "false"))
        elif v is not None:
            form.append((k, str(v)))
    return form


def _map_custom_fields(cf: dict, mapping: dict, strict: bool) -> dict:
    out = {}
    for name, value in (cf or {}).items():
        if value is None:        # Freshdesk rejects null for a typed custom field
            continue
        if name in mapping:
            target = mapping[name]
            if target == "__skip__":
                continue
            out[target] = value
        elif not strict:
            out[name] = value
    return out


def _download_attachments(ctx: Context, source_id, atts: list, kind: str) -> list[tuple[str, str]]:
    """Returns [(filename, local_path)]. Records every attachment outcome to the
    manifest. kind is 'ticket' or 'conversation' (for the manifest entity)."""
    max_bytes = ctx.cfg.attachment_max_mb * 1024 * 1024
    tmp_dir = Path(ctx.cfg.attachment_tmp_dir)
    out = []
    for att in atts or []:
        att_id = att.get("id") or att.get("name")
        name = att.get("name") or f"file_{att_id}"
        size = att.get("size") or 0
        url = att.get("attachment_url") or att.get("url")
        if not url:
            ctx.store.record_attachment(kind, source_id, att_id, name, size,
                                        "unavailable", "no url (e.g. Freshcaller-hosted recording)")
            continue
        if size and size > max_bytes:
            ctx.store.record_attachment(kind, source_id, att_id, name, size,
                                        "skipped_oversize", f"{size/1048576:.1f}MB > {ctx.cfg.attachment_max_mb}MB cap")
            continue
        dest = tmp_dir / f"{kind}_{source_id}_{att_id}_{name}"
        if ctx.src.download_to(url, dest):
            ctx.store.record_attachment(kind, source_id, att_id, name, size, "done")
            out.append((name, str(dest)))
        else:
            ctx.store.record_attachment(kind, source_id, att_id, name, size,
                                        "unavailable", "download failed (URL may have expired)")
    return out


def _post_with_attachments(client, path: str, payload: dict, files_spec: list[tuple[str, str]]):
    """POST with multipart if attachments present, else JSON. Cleans up handles."""
    if not files_spec:
        return client.post(path, json=payload)
    handles = []
    try:
        files = []
        for fname, fpath in files_spec:
            fh = open(fpath, "rb")
            handles.append(fh)
            files.append(("attachments[]", (fname, fh, "application/octet-stream")))
        return client.post(path, data=_flatten_form(payload), files=files,
                           timeout=ATTACHMENT_TIMEOUT)
    finally:
        for fh in handles:
            fh.close()


def _cleanup(files_spec: list[tuple[str, str]]) -> None:
    for _, fpath in files_spec:
        try:
            os.remove(fpath)
        except OSError:
            pass


def _create_ticket(ctx: Context, sid, payload: dict, files: list[tuple[str, str]]):
    """POST a ticket, with the two known graceful-degrade retries:
      - target without Multiple Companies rejects company_id -> retry without it;
      - target whose status stops the SLA timer rejects due_by/fr_due_by -> retry
        without them (SLA recalculates on the target anyway).
    Returns the final response. Caller cleans up `files`. Shared by the live and
    archived ticket paths so both get identical retry behavior."""
    resp = _post_with_attachments(ctx.tgt, "/tickets", payload, files)
    if (resp.status_code == 400 and "company_id" in payload
            and "company_id" in (resp.text or "")):
        ctx.log(ENTITY, sid, "WARNING", "company_dropped",
                "target rejected company_id - retrying without it")
        payload.pop("company_id")
        resp = _post_with_attachments(ctx.tgt, "/tickets", payload, files)
    if (resp.status_code == 400
            and (payload.get("due_by") or payload.get("fr_due_by"))
            and ("due_by" in (resp.text or "")
                 or "sla timer" in (resp.text or "").lower())):
        ctx.log(ENTITY, sid, "WARNING", "due_dates_dropped",
                "target rejected due_by/fr_due_by (status has no SLA timer) "
                "- retrying without them")
        payload.pop("due_by", None)
        payload.pop("fr_due_by", None)
        resp = _post_with_attachments(ctx.tgt, "/tickets", payload, files)
    return resp


# --------------------------------------------------------------- payload
def _build_payload(ctx: Context, t: dict, contact_map, agent_map, group_map,
                   company_map) -> dict:
    """t must be the ticket DETAIL payload (optionally with include=requester).
    The LIST payload has no description/attachments/requester - building from
    it silently migrates every ticket with an empty body."""
    cfg = ctx.cfg
    description = (t.get("description") or t.get("description_text") or " ")
    # As-is replication: no injected banner unless explicitly enabled. The only
    # thing a migrated ticket carries beyond source data is the marker tag.
    banner = provenance_banner(t) if cfg.provenance_banner else ""
    payload = {
        "subject": t.get("subject") or "(no subject)",
        "description": description + banner,
        "status": cfg.map_status.get(t.get("status"), t.get("status", 2)),
        "priority": cfg.map_priority.get(t.get("priority"), t.get("priority", 1)),
        "source": cfg.map_source.get(t.get("source"), t.get("source", 2)),
        "type": t.get("type"),
        "tags": (t.get("tags") or []) + [_marker_tag(t["id"])],
        "cc_emails": t.get("cc_emails") or [],
        # Due dates: only for open/pending tickets, only when BOTH are present, and
        # only if still in the FUTURE (the ticket is created now, so past dates are
        # rejected). Otherwise dropped - SLA recalculates on the target anyway.
        "due_by": t.get("due_by") if (t.get("status") in (2, 3) and t.get("due_by")
                                      and _is_future(t.get("fr_due_by"))) else None,
        "fr_due_by": t.get("fr_due_by") if (t.get("status") in (2, 3) and t.get("due_by")
                                            and _is_future(t.get("fr_due_by"))) else None,
        "custom_fields": _map_custom_fields(t.get("custom_fields"), cfg.map_custom_fields,
                                            cfg.custom_field_strict),
    }

    # Optionally preserve the original created date in a dedicated custom field
    # (the field must pre-exist on the target; unknown keys reject the record).
    if cfg.created_date_custom_field and t.get("created_at"):
        payload["custom_fields"][cfg.created_date_custom_field] = t["created_at"]

    # Requester: prefer the mapped target contact id, else fall back to email
    # (present because the detail is fetched with include=requester - covers
    # agent-raised tickets, whose requesters are not in the contact idmap).
    src_requester = t.get("requester_id")
    tgt_requester = contact_map.get(str(src_requester)) if src_requester else None
    if tgt_requester:
        payload["requester_id"] = int(tgt_requester)
    else:
        email = (t.get("requester") or {}).get("email") or t.get("email")
        if email:
            payload["email"] = email
        else:
            ctx.log(ENTITY, t["id"], "WARNING", "no_requester",
                    "no mapped requester and no email - ticket may be rejected")

    # Responder (agent), group and company via idmaps.
    src_responder = t.get("responder_id")
    if src_responder and agent_map.get(str(src_responder)):
        payload["responder_id"] = int(agent_map[str(src_responder)])
    src_group = t.get("group_id")
    if src_group and group_map.get(str(src_group)):
        payload["group_id"] = int(group_map[str(src_group)])
    src_company = t.get("company_id")
    if src_company and company_map.get(str(src_company)):
        payload["company_id"] = int(company_map[str(src_company)])

    # Brand/product is matched by name in the full tool; not mapped here -> drop+log.
    if t.get("product_id"):
        ctx.log(ENTITY, t["id"], "INFO", "product_skipped",
                "product/brand not mapped in this build - left default")

    return strip_empty(payload)


# ----------------------------------------------------------- conversations
def _replay_conversations(ctx: Context, src_ticket_id, tgt_ticket_id,
                          agent_map, contact_map, convs=None) -> int:
    """Returns the number of conversations that FAILED to post. Callers keep
    the ticket 'partial' when > 0, so a re-run retries just the missing ones
    (the conversations table dedups the ones that made it). Pass `convs` to
    replay an already-fetched list (the archived path pre-fetches them, since
    archived conversations aren't at the normal /tickets/{id}/conversations)."""
    as_notes = ctx.cfg.conversations_as_notes
    conv_failed = 0
    if convs is None:
        try:
            convs = list(ctx.src.paginate(f"/tickets/{src_ticket_id}/conversations"))
        except FreshdeskApiError as e:
            ctx.log(ENTITY, src_ticket_id, "WARNING", "conversations_fetch_failed", str(e))
            return 1

    for conv in convs:
        cid = conv.get("id")
        if ctx.store.conversation_done(src_ticket_id, cid):
            continue

        is_private = bool(conv.get("private", False))
        is_incoming = bool(conv.get("incoming", False))
        # source code 2 = note; 0 = reply (email). Other codes (forward, social,
        # phone) are replayed as notes - the target can't re-send those channels.
        is_note = conv.get("source") == 2

        # Author attribution: notes accept user_id (agent OR contact);
        # /reply accepts only an AGENT user_id.
        src_user = conv.get("user_id")
        agent_user = agent_map.get(str(src_user)) if src_user else None
        tgt_user = agent_user or (contact_map.get(str(src_user)) if src_user else None)

        body = conv.get("body") or conv.get("body_text") or " "
        if ctx.cfg.provenance_banner:
            author_hint = "" if tgt_user else (conv.get("from_email") or "")
            body += note_provenance_line(conv, author_hint)
        files = (_download_attachments(ctx, f"{src_ticket_id}.{cid}",
                                       conv.get("attachments"), "conversation")
                 if ctx.cfg.include_attachments else [])

        if as_notes:
            # Legacy safe mode: everything becomes a note (notes never email).
            path = f"/tickets/{tgt_ticket_id}/notes"
            payload = strip_empty({
                "body": body,
                "private": False if ctx.cfg.force_public_notes else is_private,
                "user_id": int(tgt_user) if tgt_user else None})
        elif is_incoming:
            # Customer message: replicate as an INCOMING note authored by the
            # mapped contact - renders in the thread as the customer's reply.
            path = f"/tickets/{tgt_ticket_id}/notes"
            payload = strip_empty({
                "body": body, "incoming": True, "private": False,
                "user_id": int(tgt_user) if tgt_user else None})
        elif is_private or is_note:
            # Internal/private (or public) note stays exactly that.
            path = f"/tickets/{tgt_ticket_id}/notes"
            payload = strip_empty({
                "body": body, "private": is_private,
                "user_id": int(tgt_user) if tgt_user else None})
        else:
            # Agent public reply: replicate as a REAL reply. Requires target
            # notifications OFF, otherwise this emails the requester.
            path = f"/tickets/{tgt_ticket_id}/reply"
            payload = strip_empty({
                "body": body,
                "user_id": int(agent_user) if agent_user else None})

        resp = _post_with_attachments(ctx.tgt, path, payload, files)
        _cleanup(files)
        if resp.status_code in (200, 201):
            ctx.store.mark_conversation(src_ticket_id, cid, "done")
        else:
            conv_failed += 1
            ctx.log(ENTITY, src_ticket_id, "WARNING", "conversation_failed",
                    f"conv {cid}: {resp.status_code} {resp.text[:150]}")
    return conv_failed


# ------------------------------------------------------ archived tickets (D2)
def archived_feature_state(client) -> str:
    """Probe the archived-ticket endpoint to classify the SOURCE account:
      'unavailable' - plan lacks the Archive Tickets feature (403 require_feature):
                      no archived tickets can exist, nothing is being missed.
      'available'   - endpoint answers 200/404: archived tickets MAY exist and the
                      normal LIST enumeration silently omits them.
      'unknown'     - anything else (network/unexpected)."""
    try:
        r = client.get_raw("/tickets/archived/1")
    except Exception:
        return "unknown"
    if r.status_code == 403 and "require_feature" in (r.text or ""):
        return "unavailable"
    if r.status_code in (200, 404):
        return "available"
    return "unknown"


def _archived_conversations(ctx: Context, sid, det: dict):
    """Best-effort archived-conversation fetch: prefer any embedded in the detail,
    then the archived sub-endpoint, then the normal one. Archived conversation
    access varies by plan, so this stays defensive rather than assuming a path."""
    if det.get("conversations"):
        return det["conversations"]
    for ep in (f"/tickets/archived/{sid}/conversations", f"/tickets/{sid}/conversations"):
        try:
            convs = list(ctx.src.paginate(ep))
            if convs:
                return convs
        except FreshdeskApiError:
            continue
    return []


def _migrate_archived(ctx: Context, contact_map, agent_map, group_map,
                      company_map, markers: dict) -> dict:
    """Opt-in (cfg.archived_scan) ingestion of archived tickets, which the LIST
    endpoint never returns. Sweeps an id range, fetches each via
    GET /tickets/archived/{id}, and reuses the SAME payload/create/replay path as
    live tickets. Dedups against the idmap + target markers, so it is safe to
    re-run and safe to run after the live pass."""
    cfg = ctx.cfg
    state = archived_feature_state(ctx.src)
    if state == "unavailable":
        ctx.logger.info("Archived scan: the Archive Tickets feature is not on the SOURCE "
                        "plan - no archived tickets exist, nothing to ingest.")
        return {"feature": "unavailable", "scanned": 0, "created": 0, "failed": 0}
    if state == "unknown":
        ctx.logger.warning("Archived scan: could not confirm the archived endpoint - skipping.")
        return {"feature": "unknown", "scanned": 0, "created": 0, "failed": 0}

    id_min = int(cfg.archived_id_min or 1)
    id_max = cfg.archived_id_max
    if not id_max:
        known = [int(k) for k in ctx.store.target_map("ticket") if str(k).isdigit()]
        id_max = max(known) if known else 0
    if not id_max or id_max < id_min:
        ctx.logger.warning("Archived scan: no archived_id_max and no known ticket ids to "
                           "derive one from. Set tickets.archived_id_max. Skipping.")
        return {"feature": state, "scanned": 0, "created": 0, "failed": 0}

    span = id_max - id_min + 1
    ctx.logger.info(f"Archived scan: probing ids {id_min}-{id_max} ({span} candidates, "
                    "one GET each for ids not already migrated). This can be slow on a "
                    "large old account - bound it with archived_id_min/max if needed.")
    scanned = created = failed = not_archived = 0
    hb = Heartbeat(ctx.logger, "archived")
    for sid in range(id_min, id_max + 1):
        rec = ctx.store.get_record(ENTITY, sid)
        if rec and rec["status"] == "done":
            continue                                    # already migrated (live or prior)
        if not rec and markers.get(str(sid)):
            ctx.store.upsert(ENTITY, sid, markers[str(sid)], status="done")
            continue                                    # already in target (marker)
        scanned += 1
        r = ctx.src.get_raw(f"/tickets/archived/{sid}")
        if r.status_code == 404:
            not_archived += 1                           # id isn't an archived ticket
            continue
        if r.status_code == 403:
            ctx.logger.warning("Archived scan: 403 mid-scan (feature gate) - aborting.")
            break
        if r.status_code >= 400:
            failed += 1
            ctx.log(ENTITY, sid, "WARNING", "archived_fetch_failed",
                    f"{r.status_code} {r.text[:150]}")
            continue
        det = r.json()
        try:
            payload = _build_payload(ctx, det, contact_map, agent_map, group_map, company_map)
            files = (_download_attachments(ctx, sid, det.get("attachments"), "ticket")
                     if cfg.include_attachments else [])
            resp = _create_ticket(ctx, sid, payload, files)
            _cleanup(files)
            if resp.status_code not in (200, 201):
                ctx.store.upsert(ENTITY, sid, None, name=det.get("subject", ""),
                                 status="failed",
                                 error=f"HTTP {resp.status_code}: {resp.text[:200]}")
                ctx.log(ENTITY, sid, "ERROR", "archived_create_failed",
                        f"{resp.status_code} {resp.text[:200]}")
                failed += 1
                continue
            tgt_id = resp.json()["id"]
            ctx.store.upsert(ENTITY, sid, tgt_id, name=det.get("subject", ""), status="partial")
            conv_failed = 0
            if cfg.include_conversations:
                convs = _archived_conversations(ctx, sid, det)
                conv_failed = _replay_conversations(ctx, sid, tgt_id, agent_map,
                                                    contact_map, convs=convs)
            _restore_status(ctx, tgt_id, payload.get("status"), sid)
            ctx.store.upsert(ENTITY, sid, tgt_id, name=det.get("subject", ""),
                             status="done" if not conv_failed else "partial",
                             error="" if not conv_failed else f"{conv_failed} conversation(s) failed")
            created += 1
        except Exception as e:
            ctx.logger.exception(f"Archived ticket {sid} crashed")
            ctx.store.upsert(ENTITY, sid, None, name=det.get("subject", ""),
                             status="failed", error=f"exception: {e}"[:250])
            failed += 1
        hb.beat(scanned, created=created, failed=failed, not_archived=not_archived)

    ctx.logger.info(f"Archived scan: range {id_min}-{id_max} scanned={scanned} "
                    f"created={created} failed={failed} not_archived={not_archived}")
    return {"feature": state, "scanned": scanned, "created": created,
            "failed": failed, "not_archived": not_archived}


# ------------------------------------------------------------------- run
def run(ctx: Context) -> dict:
    ctx.logger.info("=" * 70)
    ctx.logger.info("PHASE: Tickets (+ conversations + attachments)")

    from .custom_fields import ensure_cf_map
    ensure_cf_map(ctx, "ticket")  # build value-map even on --only tickets runs

    contact_map = ctx.store.target_map("contact")
    agent_map = ctx.store.target_map("agent")
    group_map = ctx.store.target_map("group")
    company_map = ctx.store.target_map("company")

    markers = {}
    if ctx.cfg.tickets_check_target_markers:
        ctx.logger.info("Prefetching target ticket markers (dedup against existing tickets)...")
        markers = _prefetch_target_markers(ctx)
        ctx.logger.info(f"  found {len(markers)} already-migrated ticket(s) in target")

    limit = ctx.cfg.ticket_limit
    filters = ctx.cfg.ticket_filters
    if filters:
        ctx.logger.info(f"Match-wise filter active: {filters}")

    # Delta mode: re-sync conversations/status of already-done tickets whose
    # source updated_at is newer than the last tickets run (new tickets are
    # picked up by the normal rescan either way).
    delta_since = None
    if ctx.cfg.delta:
        delta_since = ctx.store.get_cursor("tickets_last_run_started_at")
        if delta_since:
            ctx.logger.info(f"Delta mode: re-syncing tickets updated since {delta_since}")
        else:
            ctx.logger.warning("Delta mode requested but no previous tickets run "
                               "recorded - running as a normal full pass.")
    run_started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    created = resumed = skipped = failed = filtered = delta_synced = 0
    processed = 0
    hb = Heartbeat(ctx.logger, "tickets")

    for t in ctx.src.iter_tickets(ctx.cfg.tickets_updated_since, ctx.cfg.include_spam):
        if not _matches_filters(t, filters):
            filtered += 1
            continue
        # The limit counts actual WORK (created/resumed/failed), not records
        # skipped as already done - so `--limit 50` on a resume run still
        # migrates 50 new tickets instead of re-counting old ones.
        if limit and (created + resumed + failed + delta_synced) >= limit:
            ctx.logger.info(f"Reached ticket_limit={limit}; stopping.")
            break
        processed += 1
        sid = t["id"]

        rec = ctx.store.get_record(ENTITY, sid)
        if rec and rec["status"] == "done":
            if (delta_since and rec["target_id"]
                    and (t.get("updated_at") or "") > delta_since):
                tgt_id = int(rec["target_id"])
                conv_failed = _replay_conversations(ctx, sid, tgt_id, agent_map, contact_map)
                _restore_status(ctx, tgt_id, ctx.cfg.map_status.get(t.get("status"), t.get("status")), sid)
                if conv_failed:
                    ctx.store.upsert(ENTITY, sid, tgt_id, name=t.get("subject", ""),
                                     status="partial", error=f"{conv_failed} conversation(s) failed")
                ctx.log(ENTITY, sid, "INFO", "delta_synced",
                        f"re-synced conversations/status -> {tgt_id}")
                delta_synced += 1
            else:
                skipped += 1
            continue

        # Layer 2: already present in target (tagged by an earlier run or the V1 tool)?
        if not rec and markers.get(str(sid)):
            tgt_id = markers[str(sid)]
            ctx.store.upsert(ENTITY, sid, tgt_id, name=t.get("subject", ""), status="done")
            ctx.log(ENTITY, sid, "INFO", "matched", f"already in target (marker) -> {tgt_id}")
            skipped += 1
            continue

        try:
            if rec and rec["status"] == "partial" and rec["target_id"]:
                # Ticket already created; just finish its conversations.
                tgt_id = int(rec["target_id"])
                conv_failed = _replay_conversations(ctx, sid, tgt_id, agent_map, contact_map)
                _restore_status(ctx, tgt_id, ctx.cfg.map_status.get(t.get("status"), t.get("status")), sid)
                status = "done" if not conv_failed else "partial"
                ctx.store.upsert(ENTITY, sid, tgt_id, name=t.get("subject", ""), status=status,
                                 error="" if not conv_failed else f"{conv_failed} conversation(s) failed")
                resumed += 1
                continue

            # The LIST payload has no description/attachments/requester - fetch
            # the DETAIL (with requester embedded) before building the payload.
            det = ctx.src.get(f"/tickets/{sid}", params={"include": "requester"})

            payload = _build_payload(ctx, det, contact_map, agent_map, group_map, company_map)
            ticket_files = (_download_attachments(ctx, sid, det.get("attachments"), "ticket")
                            if ctx.cfg.include_attachments else [])
            resp = _create_ticket(ctx, sid, payload, ticket_files)
            _cleanup(ticket_files)

            if resp.status_code not in (200, 201):
                ctx.store.upsert(ENTITY, sid, None, name=det.get("subject", ""), status="failed",
                                 error=f"HTTP {resp.status_code}: {resp.text[:250]}")
                ctx.log(ENTITY, sid, "ERROR", "create_failed", f"{resp.status_code} {resp.text[:250]}")
                failed += 1
                continue

            tgt_id = resp.json()["id"]
            # Record 'partial' WITH target id BEFORE conversations - resume safety.
            ctx.store.upsert(ENTITY, sid, tgt_id, name=det.get("subject", ""), status="partial")
            conv_failed = 0
            if ctx.cfg.include_conversations:
                conv_failed = _replay_conversations(ctx, sid, tgt_id, agent_map, contact_map)
                _restore_status(ctx, tgt_id, payload.get("status"), sid)
            status = "done" if not conv_failed else "partial"
            ctx.store.upsert(ENTITY, sid, tgt_id, name=det.get("subject", ""), status=status,
                             error="" if not conv_failed else f"{conv_failed} conversation(s) failed")
            created += 1

        except FreshdeskApiError as e:
            ctx.store.upsert(ENTITY, sid, None, name=t.get("subject", ""), status="failed",
                             error=str(e)[:250])
            ctx.log(ENTITY, sid, "ERROR", "exception", str(e))
            failed += 1
        except Exception as e:  # never let one ticket kill the run
            ctx.logger.exception(f"Ticket {sid} crashed")
            ctx.store.upsert(ENTITY, sid, None, name=t.get("subject", ""), status="failed",
                             error=f"exception: {e}"[:250])
            failed += 1

        hb.beat(processed, total=limit, created=created, resumed=resumed,
                skipped=skipped, failed=failed, delta=delta_synced)

    hb.beat(processed, force=True, total=limit, created=created, resumed=resumed,
            skipped=skipped, failed=failed, delta=delta_synced)
    # Remember when this pass started so a later --delta run knows its baseline.
    ctx.store.set_cursor("tickets_last_run_started_at", run_started_at)
    ctx.logger.info(f"Tickets: created={created} resumed={resumed} "
                    f"skipped={skipped} failed={failed} delta_synced={delta_synced} "
                    f"filtered_out={filtered} (processed={processed})")

    result = {"created": created, "resumed": resumed, "skipped": skipped,
              "failed": failed, "delta_synced": delta_synced, "filtered_out": filtered}

    # D2: archived tickets are invisible to the LIST endpoint above. Sweep them
    # in only when explicitly enabled (and never under a --limit sample run).
    if ctx.cfg.archived_scan and not ctx.cfg.ticket_limit:
        arch = _migrate_archived(ctx, contact_map, agent_map, group_map, company_map, markers)
        result["archived"] = arch
        result["created"] += arch.get("created", 0)
        result["failed"] += arch.get("failed", 0)
    elif ctx.cfg.archived_scan and ctx.cfg.ticket_limit:
        ctx.logger.info("Archived scan requested but skipped under --limit "
                        "(run a full unlimited pass to include archived tickets).")

    return result
