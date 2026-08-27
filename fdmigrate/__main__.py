"""CLI entrypoint.

    python -m fdmigrate check   --config config.yaml
    python -m fdmigrate run     --config config.yaml [--only contacts,tickets] [--limit 500]
    python -m fdmigrate verify  --config config.yaml
    python -m fdmigrate status  --config config.yaml

`run` is fully resumable: re-running picks up from the SQLite checkpoint and
skips everything already done. To run it as a long background job that survives
a closed terminal:
    Windows : Start-Process -NoNewWindow python "-m fdmigrate run --config config.yaml"
    Linux   : nohup python -m fdmigrate run --config config.yaml &> run.out &
    Docker  : docker run -v $PWD/data:/data fdmigrate run --config /data/config.yaml
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .config import ConfigError, load_config
from .logs import setup_logging
from .reconcile import completeness_report, deep_verify, summary_lines, write_reports
from .runner import build_clients, check, run_auto, run_migration, run_rollback
from .store import Store


def _state_dir(args) -> Path:
    d = Path(args.state_dir or os.getenv("FDMIG_STATE_DIR", "."))
    d.mkdir(parents=True, exist_ok=True)
    return d


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="fdmigrate",
                                     description="Standalone Freshdesk -> Freshdesk migrator")
    parser.add_argument("command",
                        choices=["check", "run", "auto", "verify", "status",
                                 "fix-assignments", "retry", "reset", "rollback"])
    parser.add_argument("--config", required=True)
    parser.add_argument("--only", help="comma-separated phases to run (e.g. contacts,tickets)")
    parser.add_argument("--limit", type=int, help="override ticket_limit for a sample run")
    parser.add_argument("--delta", action="store_true",
                        help="delta catch-up: also re-sync conversations/status of already-"
                             "migrated tickets updated in the source since the last run")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="run: reads are real, but every target write is logged NOT sent "
                             "- validate a client config end-to-end with zero writes")
    parser.add_argument("--entity", help="reset: which entity's state to forget "
                        "(e.g. ticket,contact); retry: limit the retry to this entity")
    parser.add_argument("--sample", type=int, default=25,
                        help="auto: ticket sample size for the pre-full-run fidelity gate "
                             "(default 25)")
    parser.add_argument("--force", action="store_true",
                        help="auto: proceed even if value-coverage gaps are found "
                             "(they will fail the affected tickets)")
    parser.add_argument("--yes", action="store_true",
                        help="rollback: confirm actual deletion (without it, rollback "
                             "only previews what it would delete)")
    parser.add_argument("--deep", action="store_true",
                        help="verify: also run field-level diff on a ticket sample + a "
                             "completeness pass over the source (proves nothing was missed)")
    parser.add_argument("--completeness", help="verify --deep: comma-separated entities to "
                        "run the source-vs-idmap completeness pass on (default: tickets)")
    parser.add_argument("--state-dir", help="dir for the SQLite DB / reports / logs (default: cwd or $FDMIG_STATE_DIR)")
    parser.add_argument("--db", help="SQLite path (default: <state-dir>/migration.db)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    state = _state_dir(args)
    logger = setup_logging(state / "logs" / "fdmigrate.log", args.verbose)

    # status/reset are local-only (SQLite state); they don't need credentials.
    needs_clients = args.command not in ("status", "reset")
    try:
        cfg = load_config(args.config, validate=needs_clients)
    except ConfigError as e:
        logger.error(str(e))
        return 1

    # Reports go under the state dir unless the config set absolute paths.
    for attr in ("report_path", "manifest_path", "failures_path", "spot_check_path"):
        val = getattr(cfg, attr)
        if not os.path.isabs(val):
            setattr(cfg, attr, str(state / val))

    db_path = args.db or str(state / "migration.db")

    if args.command == "check":
        return check(cfg, logger)

    store = Store(db_path)
    try:
        if args.command == "run":
            only = [s.strip() for s in args.only.split(",")] if args.only else None
            return run_migration(cfg, store, logger, only=only,
                                 ticket_limit_override=args.limit, delta=args.delta,
                                 dry_run=args.dry_run)

        if args.command == "auto":
            return run_auto(cfg, store, logger, sample=args.sample, do_delta=args.delta,
                            force=args.force, dry_run=args.dry_run)

        if args.command == "rollback":
            return run_rollback(cfg, store, logger, confirm=args.yes, dry_run=args.dry_run)

        if args.command == "retry":
            # Clear failed/partial state (optionally for one entity) then re-run:
            # 'done' rows still skip, so only the previously-failed are reattempted.
            ent = args.entity.strip() if args.entity else None
            cleared = store.clear_failed(ent)
            logger.info(f"retry: cleared {cleared} failed/partial row(s)"
                        f"{f' for {ent}' if ent else ''}; re-running.")
            only = [s.strip() for s in args.only.split(",")] if args.only else None
            return run_migration(cfg, store, logger, only=only,
                                 ticket_limit_override=args.limit, dry_run=args.dry_run)

        if args.command == "reset":
            if not args.entity:
                logger.error("reset requires --entity (e.g. --entity ticket). "
                             "This forgets that entity's migration state so it re-migrates.")
                return 1
            for ent in (e.strip() for e in args.entity.split(",")):
                n = store.reset_entity(ent)
                logger.warning(f"reset: forgot {n} '{ent}' idmap row(s). Target-side marker "
                               "dedup still prevents duplicates on the next run.")
            return 0

        if args.command == "verify":
            src, tgt = build_clients(cfg, logger)
            for line in summary_lines(store):
                logger.info(line)
            write_reports(store, cfg, src, tgt, logger)
            if args.deep:
                entities = (tuple(s.strip() for s in args.completeness.split(","))
                            if args.completeness else ("ticket",))
                completeness_report(store, cfg, src, logger, entities=entities)
                deep_verify(store, cfg, src, tgt, logger)
            return 0

        if args.command == "status":
            for line in summary_lines(store):
                logger.info(line)
            return 0

        if args.command == "fix-assignments":
            from . import backfill
            from .phases.base import Context
            src, tgt = build_clients(cfg, logger)
            ctx = Context(src=src, tgt=tgt, store=store, cfg=cfg, logger=logger)
            result = backfill.run(ctx)
            logger.info(f"fix-assignments result: {result}")
            return 0
    finally:
        store.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
