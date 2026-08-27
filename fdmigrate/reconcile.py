"""Reconciliation: turn the state DB into auditable proof of what migrated.

Produces:
  - a printed counts summary (done/partial/failed per entity)
  - failures.csv          every record that failed, with the error
  - attachment_manifest.csv  every attachment NOT fully migrated, with the reason
  - reconciliation.csv    the FULL accounting - one row per source record of
                          every entity, with its target id / status / error,
                          so every source ticket is either mapped or a
                          documented exception
  - ticket_spot_check.csv a live source-vs-target API comparison on a random
                          sample of migrated tickets (subject + conv counts)
"""
from __future__ import annotations

import csv
import random
import re
from pathlib import Path

from .client import FreshdeskClient, FreshdeskApiError
from .store import Store


def _norm_text(s: str | None) -> str:
    """Collapse HTML tags + whitespace to a comparable plain string. Used so a
    migrated body still 'matches' its source even though the HTML differs slightly
    (and a provenance banner may be appended to the target)."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip().lower()


def _write_csv(path: str, header: list[str], rows: list) -> int:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)
    return len(rows)


def summary_lines(store: Store) -> list[str]:
    out = ["", "RECONCILIATION SUMMARY", "-" * 40]
    counts = store.counts()
    for entity in sorted(counts):
        st = counts[entity]
        out.append(f"{entity:16} done={st.get('done',0):<7} "
                   f"partial={st.get('partial',0):<5} failed={st.get('failed',0)}")
    return out


def write_reports(store: Store, cfg, src: FreshdeskClient, tgt: FreshdeskClient,
                  logger, ticket_sample: int | None = None) -> None:
    # Failures
    fails = store.failures()
    n = _write_csv(cfg.failures_path, ["entity", "source_id", "name", "error"],
                   [(r["entity"], r["source_id"], r["name"], r["error"]) for r in fails])
    logger.info(f"Failures report: {cfg.failures_path} ({n} rows)")

    # Attachment manifest (everything not 'done')
    man = store.attachment_manifest()
    n = _write_csv(cfg.manifest_path,
                   ["entity", "source_id", "att_id", "name", "size_bytes", "status", "reason"],
                   [(r["entity"], r["source_id"], r["att_id"], r["name"],
                     r["size_bytes"], r["status"], r["reason"]) for r in man])
    logger.info(f"Attachment manifest (issues only): {cfg.manifest_path} ({n} rows)")

    # FULL accounting: one row per source record across all entities - every
    # source ticket maps to a target ticket or to a documented exception.
    rows = [(r["entity"], r["source_id"], r["target_id"], r["name"],
             r["status"], r["error"]) for r in store.all_records()]
    n = _write_csv(cfg.report_path,
                   ["entity", "source_id", "target_id", "name", "status", "error"],
                   rows)
    unresolved = sum(1 for r in rows if r[4] != "done")
    logger.info(f"Full reconciliation: {cfg.report_path} ({n} rows, "
                f"{n - unresolved} done, {unresolved} exceptions)")

    # Ticket spot-check: live API comparison on a random sample.
    if ticket_sample is None:
        ticket_sample = getattr(cfg, "spot_check_sample", 25)
    pairs = [(sid, tid) for sid, tid in store.target_map("ticket").items()]
    if not pairs or ticket_sample <= 0:
        return
    sample = random.sample(pairs, min(ticket_sample, len(pairs)))
    rows = []
    for sid, tid in sample:
        try:
            s = src.get(f"/tickets/{sid}")
            t = tgt.get(f"/tickets/{tid}")
            subj_match = (s.get("subject") or "") == (t.get("subject") or "")
            sc = len(list(src.paginate(f"/tickets/{sid}/conversations")))
            tc = len(list(tgt.paginate(f"/tickets/{tid}/conversations")))
            rows.append([sid, tid, subj_match, sc, tc, "ok" if subj_match and sc == tc else "drift"])
        except FreshdeskApiError as e:
            rows.append([sid, tid, "", "", "", f"fetch_failed:{e.status_code}"])
    n = _write_csv(cfg.spot_check_path,
                   ["source_ticket", "target_ticket", "subject_match", "src_convs", "tgt_convs", "status"],
                   rows)
    clean = sum(1 for r in rows if r[5] == "ok")
    logger.info(f"Ticket spot-check: {cfg.spot_check_path} - {clean}/{len(rows)} clean")


# ---------------------------------------------------------------------------
# D1 - COMPLETENESS: prove every SOURCE record is accounted for.
# The reconciliation.csv above lists what the tool ATTEMPTED; it cannot reveal
# a source record that was never enumerated (archived ticket, a filter, or a
# windowing edge). This re-reads the source id-space and diffs it against the
# idmap, so a missing record shows up as MISSING instead of silently vanishing.
# ---------------------------------------------------------------------------
def _source_ids(cfg, src: FreshdeskClient, entity: str):
    """Yield source ids for an entity using the same enumeration the run used
    (LIST endpoints only - no per-record detail fetch, so this is cheap:
    ~1 API call per 100 records)."""
    if entity == "ticket":
        for t in src.iter_tickets(cfg.tickets_updated_since, cfg.include_spam):
            yield t["id"]
    elif entity == "contact":
        for c in src.iter_contacts(cfg.contacts_updated_since):
            yield c["id"]
    elif entity == "company":
        for c in src.paginate("/companies"):
            yield c["id"]


def completeness_report(store: Store, cfg, src: FreshdeskClient, logger,
                        entities=("ticket",)) -> dict:
    """For each entity, count the SOURCE id-space and diff against the idmap.
    Writes completeness.csv (one row per NOT-fully-migrated source id) and logs a
    PASS/FAIL line per entity. Returns {entity: {source, done, partial, failed,
    missing}}."""
    out_path = str(Path(cfg.report_path).parent / "completeness.csv")
    rows: list = []
    totals: dict = {}
    for entity in entities:
        source = done = partial = failed = missing = 0
        try:
            for sid in _source_ids(cfg, src, entity):
                source += 1
                rec = store.get_record(entity, sid)
                if not rec:
                    missing += 1
                    rows.append([entity, sid, "MISSING", ""])
                elif rec["status"] == "done":
                    done += 1
                elif rec["status"] == "partial":
                    partial += 1
                    rows.append([entity, sid, "partial", rec["error"] or ""])
                else:
                    failed += 1
                    rows.append([entity, sid, "failed", rec["error"] or ""])
        except FreshdeskApiError as e:
            logger.warning(f"Completeness[{entity}]: source enumeration failed ({e}); "
                           "cannot certify this entity.")
            continue
        totals[entity] = {"source": source, "done": done, "partial": partial,
                          "failed": failed, "missing": missing}
        unaccounted = partial + failed + missing
        verdict = "PASS" if unaccounted == 0 else "INCOMPLETE"
        logger.info(f"Completeness[{entity}]: {verdict} - source={source} done={done} "
                    f"partial={partial} failed={failed} MISSING={missing}")
        if entity == "ticket":
            # Honesty: this enumerates via the LIST endpoint, which OMITS archived
            # tickets - so a PASS certifies LIVE tickets only. Archived coverage is
            # separate (tickets.archived_scan, D2).
            logger.info("Completeness[ticket]: scope = LIVE (non-archived) tickets. "
                        "Archived tickets are not in the list API; they are covered "
                        "only when tickets.archived_scan ran.")
    _write_csv(out_path, ["entity", "source_id", "status", "detail"], rows)
    logger.info(f"Completeness report: {out_path} ({len(rows)} unaccounted-for rows)")
    return totals


# ---------------------------------------------------------------------------
# D3 - DEEP VERIFY: field-level source-vs-target diff on a sample of tickets.
# The spot-check only compares subject + conversation count. This compares the
# fields that actually matter for fidelity, applying the same value maps the
# migration used, so a "clean" row is a real certificate.
# ---------------------------------------------------------------------------
def _ticket_cf_name_map(store, cfg, src, tgt, logger) -> dict:
    """Build the source->target custom-field NAME map read-only (the verify path
    doesn't run the custom_fields phase). Empty on failure - CF diff is skipped."""
    try:
        from .phases.base import Context
        from .phases.custom_fields import build_name_map
        ctx = Context(src=src, tgt=tgt, store=store, cfg=cfg, logger=logger)
        return build_name_map(ctx, "ticket") or {}
    except Exception as e:
        logger.warning(f"deep-verify: could not build custom-field map ({e}); skipping CF diff")
        return {}


def _compare_ticket(s: dict, t: dict, cfg, agent_map, group_map, cf_name_map) -> list[str]:
    """Return a list of mismatched field names (empty = clean). Only flags a
    field when the source had a value that SHOULD have carried over - an
    unmapped agent/group or a strict-dropped custom field is not a mismatch."""
    bad: list[str] = []

    if (s.get("subject") or "") != (t.get("subject") or ""):
        bad.append("subject")

    s_body = _norm_text(s.get("description_text") or s.get("description"))
    t_body = _norm_text(t.get("description_text") or t.get("description"))
    if s_body and not (t_body == s_body or s_body in t_body):
        bad.append("description")

    if cfg.map_status.get(s.get("status"), s.get("status")) != t.get("status"):
        bad.append("status")
    if cfg.map_priority.get(s.get("priority"), s.get("priority")) != t.get("priority"):
        bad.append("priority")
    if cfg.map_source.get(s.get("source"), s.get("source")) != t.get("source"):
        bad.append("source")
    if (s.get("type") or None) != (t.get("type") or None):
        bad.append("type")

    s_email = ((s.get("requester") or {}).get("email") or "").lower()
    t_email = ((t.get("requester") or {}).get("email") or "").lower()
    if s_email and s_email != t_email:
        bad.append("requester_email")

    src_resp = s.get("responder_id")
    want_resp = agent_map.get(str(src_resp)) if src_resp else None
    if want_resp and int(want_resp) != (t.get("responder_id") or 0):
        bad.append("responder")

    src_grp = s.get("group_id")
    want_grp = group_map.get(str(src_grp)) if src_grp else None
    if want_grp and int(want_grp) != (t.get("group_id") or 0):
        bad.append("group")

    s_tags = {str(x).lower() for x in (s.get("tags") or [])}
    t_tags = {str(x).lower() for x in (t.get("tags") or [])}
    if not s_tags <= t_tags:                      # target also carries the marker tag
        bad.append("tags")

    s_cf = s.get("custom_fields") or {}
    t_cf = t.get("custom_fields") or {}
    for sname, sval in s_cf.items():
        if sval is None:
            continue
        tname = cf_name_map.get(sname)
        if not tname:                              # strict-dropped: not a mismatch
            continue
        if str(t_cf.get(tname)) != str(sval):
            bad.append(f"cf:{sname}")
    return bad


# ---------------------------------------------------------------------------
# D4 - PRE-FLIGHT VALUE/FIELD COVERAGE (runs inside `check`, no store needed).
# The ticket phase passes status/priority/source THROUGH the value maps and
# `type` through unmapped. If a source value has no valid target value, EVERY
# ticket carrying it is rejected mid-run. This enumerates the source's field
# definitions and confirms each value resolves on the target BEFORE the run,
# and summarizes which source custom fields the target still lacks. (review R5)
# ---------------------------------------------------------------------------
def _ticket_field_choices(client: FreshdeskClient) -> dict:
    """Extract the valid VALUE sets from a client's /ticket_fields:
    {'status': set[int], 'priority': set[int], 'source': set[int],
     'type': set[str]}. A dimension with no parsable choice list -> None."""
    out = {"status": None, "priority": None, "source": None, "type": None}
    for f in (client.get("/ticket_fields") or []):
        name, ch = f.get("name"), f.get("choices")
        if name == "status" and isinstance(ch, dict):
            # {"2": ["Open","Open"], ...} - the ids are the keys.
            out["status"] = {int(k) for k in ch if str(k).lstrip("-").isdigit()}
        elif name == "priority" and isinstance(ch, dict):
            out["priority"] = {int(v) for v in ch.values() if isinstance(v, int)}
        elif name == "source" and isinstance(ch, dict):
            out["source"] = {int(v) for v in ch.values() if isinstance(v, int)}
        elif name == "ticket_type":
            if isinstance(ch, list):
                out["type"] = {str(x) for x in ch}
            elif isinstance(ch, dict):
                out["type"] = {str(k) for k in ch}
    return out


def preflight_coverage(cfg, src: FreshdeskClient, tgt: FreshdeskClient, logger) -> int:
    """D4. Confirm every source ticket-field VALUE (status/priority/source/type),
    after the configured value maps, exists on the target; summarize custom-field
    coverage. Logs WARNINGs (does not fail the check). Returns the count of
    blocking value gaps (0 = clean)."""
    logger.info("Pre-flight value/field coverage (D4):")
    try:
        s = _ticket_field_choices(src)
        t = _ticket_field_choices(tgt)
    except FreshdeskApiError as e:
        logger.warning(f"  coverage check skipped (cannot read ticket_fields: {e})")
        return 0

    gaps = 0

    def check_ids(dim: str, valmap: dict) -> None:
        nonlocal gaps
        sv, tv = s.get(dim), t.get(dim)
        if sv is None or tv is None:
            logger.info(f"  {dim}: no comparable choice list; skipped.")
            return
        missing = [f"{v}->{valmap.get(v, v)}" for v in sorted(sv) if valmap.get(v, v) not in tv]
        if missing:
            gaps += len(missing)
            logger.warning(f"  {dim} GAP: source value(s) with NO valid target value "
                           f"(after mapping): {', '.join(missing)} - tickets using these "
                           f"will FAIL. Add mapping.{dim} entries or create the value on target.")
        else:
            logger.info(f"  {dim}: all {len(sv)} source value(s) resolve on target.")

    check_ids("status", cfg.map_status)
    check_ids("priority", cfg.map_priority)
    check_ids("source", cfg.map_source)

    # `type` is passed through unmapped (a string) - must exist verbatim on target.
    sv, tv = s.get("type"), t.get("type")
    if sv is not None and tv is not None:
        missing = sorted(v for v in sv if v not in tv)
        if missing:
            gaps += len(missing)
            logger.warning(f"  type GAP: source ticket type(s) missing on target: "
                           f"{', '.join(missing)} - tickets of these types may FAIL. "
                           f"Create these Type values on the target.")
        else:
            logger.info(f"  type: all {len(sv)} source type(s) exist on target.")

    # Custom fields: which source CFs the target already has vs the custom_fields
    # phase will need to create (informational - that phase auto-creates them).
    from .phases.base import Context
    from .phases.custom_fields import LIST_EP, build_name_map
    ctx = Context(src=src, tgt=tgt, store=None, cfg=cfg, logger=logger)  # type: ignore[arg-type]
    for label in ("ticket", "contact", "company"):
        try:
            src_fields = [f for f in (src.get(LIST_EP[label]) or []) if not f.get("default")]
            nm = build_name_map(ctx, label)
        except Exception as e:
            logger.warning(f"  {label} custom fields: cannot read ({e})")
            continue
        if not src_fields:
            continue
        have = sum(1 for f in src_fields if f.get("name") in nm)
        missing = [f.get("label") or f.get("name") for f in src_fields if f.get("name") not in nm]
        if missing:
            logger.info(f"  {label} custom fields: {have}/{len(src_fields)} already on target; "
                        f"custom_fields phase will create: {', '.join(missing)}")
        else:
            logger.info(f"  {label} custom fields: all {len(src_fields)} present on target.")

    if gaps:
        logger.warning(f"Pre-flight coverage: {gaps} value gap(s) would fail tickets - "
                       "resolve before the run.")
    else:
        logger.info("  value coverage clean.")
    return gaps


def deep_verify(store: Store, cfg, src: FreshdeskClient, tgt: FreshdeskClient,
                logger, sample: int | None = None) -> dict:
    """Field-level diff on a random sample of migrated tickets. Writes
    verify_deep.csv and logs clean/total. Returns {clean, mismatch, failed, total}."""
    out_path = str(Path(cfg.report_path).parent / "verify_deep.csv")
    if sample is None:
        sample = getattr(cfg, "spot_check_sample", 25)

    pairs = [(sid, tid) for sid, tid in store.target_map("ticket").items()]
    if not pairs or sample <= 0:
        logger.info("deep-verify: no migrated tickets to check.")
        return {"clean": 0, "mismatch": 0, "failed": 0, "total": 0}

    agent_map = store.target_map("agent")
    group_map = store.target_map("group")
    cf_name_map = _ticket_cf_name_map(store, cfg, src, tgt, logger)

    picked = random.sample(pairs, min(sample, len(pairs)))
    rows, clean, mismatch, failed = [], 0, 0, 0
    for sid, tid in picked:
        try:
            s = src.get(f"/tickets/{sid}", params={"include": "requester"})
            t = tgt.get(f"/tickets/{tid}", params={"include": "requester"})
            bad = _compare_ticket(s, t, cfg, agent_map, group_map, cf_name_map)
            sc = len(list(src.paginate(f"/tickets/{sid}/conversations")))
            tc = len(list(tgt.paginate(f"/tickets/{tid}/conversations")))
            if sc != tc:
                bad.append(f"conv_count({sc}!={tc})")
            status = "ok" if not bad else "MISMATCH"
            if bad:
                mismatch += 1
            else:
                clean += 1
            rows.append([sid, tid, status, ";".join(bad), sc, tc])
        except FreshdeskApiError as e:
            failed += 1
            rows.append([sid, tid, f"fetch_failed:{e.status_code}", "", "", ""])
    _write_csv(out_path, ["source_ticket", "target_ticket", "status",
                          "mismatched_fields", "src_convs", "tgt_convs"], rows)
    logger.info(f"Deep verify: {out_path} - {clean}/{len(picked)} clean, "
                f"{mismatch} mismatch, {failed} fetch-failed")
    return {"clean": clean, "mismatch": mismatch, "failed": failed, "total": len(picked)}
