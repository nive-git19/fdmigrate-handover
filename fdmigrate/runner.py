"""Orchestration: connection check + the ordered, resumable migration run."""
from __future__ import annotations

import logging
from typing import List, Optional

from .client import FreshdeskApiError, FreshdeskClient
from .config import Config
from .phases import REGISTRY
from .phases.base import Context
from .reconcile import (completeness_report, deep_verify, preflight_coverage,
                        summary_lines, write_reports)
from .store import Store


def build_clients(cfg: Config, logger: logging.Logger):
    src = FreshdeskClient(cfg.source_domain, cfg.source_key, logger,
                          label="source", rate_limit_floor=cfg.rate_limit_floor)
    # Only the TARGET can be dry-run; the source is read-only either way.
    tgt = FreshdeskClient(cfg.target_domain, cfg.target_key, logger,
                          label="target", rate_limit_floor=cfg.rate_limit_floor,
                          dry_run=getattr(cfg, "dry_run", False))
    return src, tgt


def check(cfg: Config, logger: logging.Logger) -> int:
    """Verify access to both accounts and show what the source contains."""
    src, tgt = build_clients(cfg, logger)
    logger.info("=" * 70)
    logger.info("CONNECTION CHECK")
    logger.info(f"  source: {cfg.source_domain}")
    logger.info(f"  target: {cfg.target_domain}")

    ok = True
    for label, client in (("source", src), ("target", tgt)):
        try:
            me = client.whoami()
            who = (me.get("contact") or {}).get("email") or me.get("id")
            logger.info(f"  [{label}] OK - authenticated as {who}")
        except FreshdeskApiError as e:
            logger.error(f"  [{label}] FAILED - HTTP {e.status_code}: {e.body[:150]}")
            ok = False
        except Exception as e:  # DNS / connection / timeout
            logger.error(f"  [{label}] FAILED - cannot reach account: {e}")
            ok = False

    if not ok:
        logger.error("Connection check failed. Fix domain/API keys before running.")
        return 1

    # Cheap counts for small objects; tickets are streamed (not counted up-front).
    def count(path):
        try:
            n = sum(1 for _ in src.paginate(path))
            # paginate stops at Freshdesk's 300-page cap - be honest about it.
            return f"{n}+ (30k list cap reached)" if n >= 30000 else n
        except FreshdeskApiError:
            return "?"

    logger.info("Source object counts (tickets streamed at run time):")
    for label, path in (("agents", "/agents"), ("groups", "/groups"),
                        ("companies", "/companies"), ("contacts", "/contacts")):
        logger.info(f"    {label:10}: {count(path)}")

    # Agent pre-validation: source agents with no matching target agent surface
    # HERE, not as unassigned tickets discovered mid-migration.
    def agent_email(a):
        return ((a.get("contact") or {}).get("email") or a.get("email") or "").strip().lower()

    try:
        src_agents = {agent_email(a): (a.get("contact") or {}).get("name") or ""
                      for a in src.paginate("/agents") if agent_email(a)}
        tgt_emails = {agent_email(a) for a in tgt.paginate("/agents") if agent_email(a)}
        gaps = []
        for email, name in sorted(src_agents.items()):
            want = cfg.map_agents.get(email, email)
            if want not in tgt_emails:
                mapped = f" (mapped to {want})" if want != email else ""
                gaps.append(f"{name} <{email}>{mapped}")
        if gaps:
            logger.warning(f"AGENT GAP: {len(gaps)} source agent(s) have NO matching "
                           "target agent - their tickets will migrate UNASSIGNED:")
            for g in gaps:
                logger.warning(f"    - {g}")
            logger.warning("  Create these agents in the target (or add mapping.agents "
                           "entries) BEFORE the migration run.")
        else:
            logger.info(f"  agents: all {len(src_agents)} source agent(s) resolve in the target.")
    except FreshdeskApiError as e:
        logger.warning(f"  agent pre-validation skipped (cannot list agents: {e})")

    # D4: value/field coverage - catch status/priority/source/type values that
    # have no valid target (which would fail tickets mid-run) before starting.
    try:
        preflight_coverage(cfg, src, tgt, logger)
    except Exception as e:
        logger.warning(f"  pre-flight coverage skipped ({e})")

    # D2: archived-ticket awareness. The LIST endpoint omits archived tickets, so
    # if the feature is on they'd be silently missed unless archived_scan is set.
    try:
        from .phases.tickets import archived_feature_state
        state = archived_feature_state(src)
        if state == "unavailable":
            logger.info("Archived tickets: Archive Tickets feature not on the source "
                        "plan - none exist, nothing can be missed.")
        elif state == "available":
            if cfg.archived_scan:
                logger.info("Archived tickets: feature is ON; archived_scan is ENABLED - "
                            "they will be swept in after the live pass.")
            else:
                logger.warning("Archived tickets: the source has the Archive Tickets feature "
                               "ON. Archived (old/closed) tickets are NOT returned by the list "
                               "API and would be SILENTLY MISSED. Set tickets.archived_scan: "
                               "true (with archived_id_max) to migrate them.")
    except Exception as e:
        logger.warning(f"  archived-ticket check skipped ({e})")

    logger.info("Connection check passed.")
    return 0


def run_rollback(cfg: Config, store: Store, logger: logging.Logger,
                 confirm: bool = False, dry_run: bool = False) -> int:
    """Undo a migration by deleting the target tickets THIS TOOL created - identified
    by their `fd-migration-*` marker tag, so only tool-created tickets are ever
    touched. Contacts/companies/agents are matched/updated CLIENT data and are NEVER
    deleted here. Previews unless --yes is given."""
    from .phases.base import Context
    from .phases.tickets import _prefetch_target_markers

    src, tgt = build_clients(cfg, logger)
    ctx = Context(src=src, tgt=tgt, store=store, cfg=cfg, logger=logger)
    logger.info("=" * 70)
    logger.info("ROLLBACK - delete target tickets created by this tool (marker-tagged)")
    logger.info("Scanning target for migration-marker tickets...")
    markers = _prefetch_target_markers(ctx)          # {source_id: target_id}
    n = len(markers)
    if n == 0:
        logger.info("No migration-marker tickets on the target. Nothing to roll back.")
        return 0

    logger.warning(f"Found {n} tool-created ticket(s) on the target.")
    logger.warning("Rollback deletes ONLY these tickets. It does NOT delete contacts, "
                   "companies, agents or groups (matched/updated client data) - remove "
                   "those manually if you truly need to.")
    if dry_run or not confirm:
        logger.info(f"PREVIEW: would delete {n} target ticket(s). "
                    "Re-run with --yes to actually delete them.")
        return 0

    deleted = failed = 0
    for sid, tid in markers.items():
        try:
            r = tgt.delete(f"/tickets/{tid}")
            if r.status_code in (200, 204):
                deleted += 1
                store.remove_record("ticket", sid)
            else:
                failed += 1
                logger.warning(f"  delete ticket {tid} -> HTTP {r.status_code}")
        except Exception as e:
            failed += 1
            logger.warning(f"  delete ticket {tid} error: {e}")
    logger.info("=" * 70)
    logger.info(f"ROLLBACK complete: deleted={deleted} failed={failed} of {n}.")
    return 0 if not failed else 2


def run_auto(cfg: Config, store: Store, logger: logging.Logger,
             sample: int = 25, do_delta: bool = False, force: bool = False,
             dry_run: bool = False) -> int:
    """AUTO MODE: the whole go-live sequence as one self-verifying command, with
    gates that STOP the run rather than push bad data through:
      Gate 1 connectivity -> Gate 2 value coverage -> foundation -> ticket SAMPLE
      + deep-verify gate -> full run -> completeness + deep-verify certification
      (-> optional delta). Fully resumable: re-running continues from the checkpoint.
    """
    cfg.dry_run = dry_run
    src, tgt = build_clients(cfg, logger)
    logger.info("=" * 70)
    logger.info("AUTO MODE - gated end-to-end migration")
    if dry_run:
        logger.info("(dry-run: no target writes; verification gates are skipped)")

    # Gate 1 - connectivity
    for label, client in (("source", src), ("target", tgt)):
        try:
            client.whoami()
        except Exception as e:
            logger.error(f"AUTO ABORT @ Gate 1: cannot reach {label} account: {e}")
            return 1
    logger.info("Gate 1 (connectivity): PASS")

    # Gate 2 - value/field coverage (unmapped values would fail tickets mid-run)
    try:
        gaps = preflight_coverage(cfg, src, tgt, logger)
    except Exception as e:
        logger.warning(f"coverage check skipped ({e})")
        gaps = 0
    if gaps and not force:
        logger.error(f"AUTO ABORT @ Gate 2: {gaps} value-coverage gap(s) would fail tickets. "
                     "Fix the mappings, or re-run `auto --force` to proceed anyway.")
        return 1
    logger.info(f"Gate 2 (value coverage): {'PASS' if not gaps else f'{gaps} gap(s) - FORCED'}")

    enabled = cfg.enabled_phases()

    # Phase A - foundation (everything tickets depend on), run to completion first.
    foundation = [p for p in enabled
                  if p not in ("tickets", "canned_responses", "knowledge_base")]
    if foundation:
        logger.info(f"Phase A - foundation: {foundation}")
        rc = run_migration(cfg, store, logger, only=foundation, dry_run=dry_run)
        if rc != 0:
            logger.error("AUTO ABORT: foundation phase failed. Fix, then re-run `auto` (resumes).")
            return rc

    # Gate 3 - ticket SAMPLE, field-verified BEFORE committing to the full volume.
    if "tickets" in enabled:
        logger.info(f"Phase B - ticket sample (limit {sample})")
        rc = run_migration(cfg, store, logger, only=["tickets"],
                           ticket_limit_override=sample, dry_run=dry_run)
        if rc != 0:
            logger.error("AUTO ABORT: ticket sample run failed.")
            return rc
        if not dry_run:
            res = deep_verify(store, cfg, src, tgt, logger, sample=sample)
            if res["mismatch"] or res["failed"]:
                logger.error(f"AUTO ABORT @ Gate 3: sample fidelity failed - "
                             f"{res['mismatch']} mismatch, {res['failed']} fetch-failed. "
                             "Inspect verify_deep.csv; do NOT run the full volume yet.")
                return 1
            logger.info(f"Gate 3 (sample fidelity): PASS ({res['clean']}/{res['total']} clean)")

    # Phase C - full run (foundation already done -> skipped; tickets continue; canned+kb).
    logger.info("Phase C - full migration")
    rc = run_migration(cfg, store, logger, dry_run=dry_run)
    if rc != 0:
        logger.error("AUTO ABORT: full run failed. Re-run `auto` (resumes) or `retry --failed`.")
        return rc

    # Phase D - delta catch-up (optional).
    if do_delta and not dry_run:
        logger.info("Phase D - delta catch-up")
        run_migration(cfg, store, logger, only=["tickets"], delta=True)

    # Gate 4 - completeness + deep-verify certification.
    if dry_run:
        logger.info("AUTO MODE (dry-run) complete - sequence exercised, nothing written.")
        return 0
    logger.info("Gate 4 - completeness + deep verify")
    ents = ["ticket"]
    if cfg.objects.get("contacts"):
        ents.append("contact")
    if cfg.objects.get("companies"):
        ents.append("company")
    totals = completeness_report(store, cfg, src, logger, entities=tuple(ents))
    dv = deep_verify(store, cfg, src, tgt, logger, sample=sample)
    incomplete = [e for e, v in totals.items()
                  if (v["partial"] + v["failed"] + v["missing"]) > 0]
    logger.info("=" * 70)
    if incomplete or dv["mismatch"]:
        logger.warning(f"AUTO MODE finished WITH ATTENTION: incomplete={incomplete}, "
                       f"sample_mismatches={dv['mismatch']}. Review completeness.csv / "
                       "verify_deep.csv, then `retry --failed` and re-run `auto`.")
        return 3
    logger.info("Gate 4 (certification): PASS - all entities complete, sample fidelity clean.")
    logger.info("AUTO MODE complete. Remember to re-enable target notifications/automations.")
    return 0


def run_migration(cfg: Config, store: Store, logger: logging.Logger,
                  only: Optional[List[str]] = None,
                  ticket_limit_override: Optional[int] = None,
                  delta: bool = False, dry_run: bool = False) -> int:
    cfg.dry_run = dry_run
    src, tgt = build_clients(cfg, logger)
    if ticket_limit_override is not None:
        cfg.ticket_limit = ticket_limit_override
    cfg.delta = delta
    if dry_run:
        logger.info("=" * 70)
        logger.info("DRY RUN: reads are real; every target write is logged, NOT sent. "
                    "No data will be created/updated on the target.")

    ctx = Context(src=src, tgt=tgt, store=store, cfg=cfg, logger=logger)

    phases = cfg.enabled_phases()
    if only:
        phases = [p for p in phases if p in only]
        if not phases:
            logger.error(f"None of {only} are enabled phases. Enabled: {cfg.enabled_phases()}")
            return 1

    logger.info(f"Running phases in order: {phases}")
    for name in phases:
        module = REGISTRY[name]
        try:
            result = module.run(ctx)
            logger.info(f"Phase '{name}' result: {result}")
        except FreshdeskApiError as e:
            logger.error(f"Phase '{name}' aborted on API error: {e}")
            logger.error("Re-run to resume from the last checkpoint.")
            return 2
        except KeyboardInterrupt:
            logger.info("Interrupted - progress is checkpointed. Re-run to resume.")
            return 130
        except Exception as e:  # network blip, unexpected payload, etc.
            logger.exception(f"Phase '{name}' hit an unexpected error: {e}")
            logger.error("Progress is checkpointed. Re-run to resume from here.")
            return 2

    for line in summary_lines(store):
        logger.info(line)
    try:
        write_reports(store, cfg, src, tgt, logger)
    except Exception as e:  # reporting must never mask a successful migration
        logger.warning(f"Report generation issue: {e}")

    # Consolidated end-of-run verdict + next-step hints.
    counts = store.counts()
    total_done = sum(st.get("done", 0) for st in counts.values())
    total_failed = sum(st.get("failed", 0) for st in counts.values())
    total_partial = sum(st.get("partial", 0) for st in counts.values())
    logger.info("=" * 70)
    if cfg.dry_run:
        logger.info("DRY RUN COMPLETE - nothing was written to the target.")
    logger.info(f"RUN SUMMARY: {total_done} done, {total_partial} partial, "
                f"{total_failed} failed across all entities.")
    if total_failed:
        logger.info(f"  -> {total_failed} failed. Review {cfg.failures_path}, then re-run "
                    "`retry --failed` to reattempt only those.")
    if total_partial:
        logger.info(f"  -> {total_partial} partial (some conversations pending). A normal "
                    "re-run resumes them.")
    logger.info("  -> Verify with `verify --deep` (field-level diff + completeness).")
    logger.info("Migration run complete.")
    return 0
