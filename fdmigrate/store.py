"""SQLite state store - the backbone of dedup and resume-on-crash.

Tables:
  idmap         source->target id per entity, with status (done/partial/failed)
  conversations per-ticket conversation tracking, so a resumed ticket re-adds
                only the notes/replies it hadn't finished
  attachments   per-attachment outcome (done/skipped_oversize/unavailable/failed)
  events        append-only per-record log
  cursors       arbitrary resume markers (e.g. ticket window position)

Every write is committed immediately so a kill -9 loses at most the record in
flight. Re-running the migration consults idmap and skips what's already done.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SCHEMA = """
CREATE TABLE IF NOT EXISTS idmap (
    entity     TEXT NOT NULL,
    source_id  TEXT NOT NULL,
    target_id  TEXT,
    name       TEXT,
    status     TEXT NOT NULL DEFAULT 'done',   -- done | partial | failed
    error      TEXT,
    updated_at REAL,
    PRIMARY KEY (entity, source_id)
);
CREATE TABLE IF NOT EXISTS conversations (
    ticket_source_id TEXT NOT NULL,
    conv_source_id   TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'done',
    PRIMARY KEY (ticket_source_id, conv_source_id)
);
CREATE TABLE IF NOT EXISTS attachments (
    entity     TEXT NOT NULL,
    source_id  TEXT NOT NULL,
    att_id     TEXT NOT NULL,
    name       TEXT,
    size_bytes INTEGER,
    status     TEXT NOT NULL,   -- done | skipped_oversize | unavailable | failed
    reason     TEXT,
    PRIMARY KEY (entity, source_id, att_id)
);
CREATE TABLE IF NOT EXISTS events (
    ts        REAL,
    entity    TEXT,
    source_id TEXT,
    level     TEXT,
    action    TEXT,
    message   TEXT
);
CREATE TABLE IF NOT EXISTS cursors (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL;")
        self.db.execute("PRAGMA synchronous=NORMAL;")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------- id mapping
    def get_target(self, entity: str, source_id) -> Optional[str]:
        row = self.db.execute(
            "SELECT target_id FROM idmap WHERE entity=? AND source_id=? AND status='done'",
            (entity, str(source_id)),
        ).fetchone()
        return row["target_id"] if row else None

    def get_record(self, entity: str, source_id) -> Optional[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM idmap WHERE entity=? AND source_id=?",
            (entity, str(source_id)),
        ).fetchone()

    def upsert(self, entity: str, source_id, target_id=None, name: str = "",
               status: str = "done", error: str = "") -> None:
        self.db.execute(
            """INSERT INTO idmap (entity, source_id, target_id, name, status, error, updated_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(entity, source_id) DO UPDATE SET
                 target_id=excluded.target_id, name=excluded.name,
                 status=excluded.status, error=excluded.error, updated_at=excluded.updated_at""",
            (entity, str(source_id), str(target_id) if target_id is not None else None,
             name, status, error, time.time()),
        )
        self.db.commit()

    def target_map(self, entity: str) -> Dict[str, str]:
        rows = self.db.execute(
            "SELECT source_id, target_id FROM idmap WHERE entity=? AND status='done'",
            (entity,),
        ).fetchall()
        return {r["source_id"]: r["target_id"] for r in rows}

    # ----------------------------------------------------------- conversations
    def conversation_done(self, ticket_source_id, conv_source_id) -> bool:
        return self.db.execute(
            "SELECT 1 FROM conversations WHERE ticket_source_id=? AND conv_source_id=?",
            (str(ticket_source_id), str(conv_source_id)),
        ).fetchone() is not None

    def mark_conversation(self, ticket_source_id, conv_source_id, status="done") -> None:
        self.db.execute(
            """INSERT OR REPLACE INTO conversations
               (ticket_source_id, conv_source_id, status) VALUES (?,?,?)""",
            (str(ticket_source_id), str(conv_source_id), status),
        )
        self.db.commit()

    # ------------------------------------------------------------ attachments
    def record_attachment(self, entity, source_id, att_id, name, size_bytes,
                          status, reason="") -> None:
        self.db.execute(
            """INSERT OR REPLACE INTO attachments
               (entity, source_id, att_id, name, size_bytes, status, reason)
               VALUES (?,?,?,?,?,?,?)""",
            (entity, str(source_id), str(att_id), name, size_bytes, status, reason),
        )
        self.db.commit()

    # ----------------------------------------------------------------- events
    def log_event(self, entity, source_id, level, action, message) -> None:
        self.db.execute(
            "INSERT INTO events (ts, entity, source_id, level, action, message) VALUES (?,?,?,?,?,?)",
            (time.time(), entity, str(source_id), level, action, str(message)[:1000]),
        )
        self.db.commit()

    # ---------------------------------------------------------------- cursors
    def get_cursor(self, key: str) -> Optional[str]:
        row = self.db.execute("SELECT value FROM cursors WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_cursor(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO cursors (key, value) VALUES (?,?)", (key, value)
        )
        self.db.commit()

    # ------------------------------------------------------------- reporting
    def counts(self) -> Dict[str, Dict[str, int]]:
        out: Dict[str, Dict[str, int]] = {}
        for r in self.db.execute(
            "SELECT entity, status, COUNT(*) n FROM idmap GROUP BY entity, status"
        ).fetchall():
            out.setdefault(r["entity"], {})[r["status"]] = r["n"]
        return out

    def records(self, entity: str, statuses=("done",)) -> List[sqlite3.Row]:
        """All idmap rows for an entity in the given statuses that have a target id
        (used by backfill to revisit already-created records)."""
        placeholders = ",".join("?" for _ in statuses)
        return self.db.execute(
            f"SELECT source_id, target_id, name, status FROM idmap "
            f"WHERE entity=? AND status IN ({placeholders}) AND target_id IS NOT NULL",
            (entity, *statuses),
        ).fetchall()

    def all_records(self) -> List[sqlite3.Row]:
        """Every idmap row across all entities - the full reconciliation view."""
        return self.db.execute(
            "SELECT entity, source_id, target_id, name, status, error FROM idmap "
            "ORDER BY entity, CAST(source_id AS INTEGER)"
        ).fetchall()

    def failures(self) -> List[sqlite3.Row]:
        return self.db.execute(
            "SELECT entity, source_id, name, error FROM idmap WHERE status='failed'"
        ).fetchall()

    def attachment_manifest(self) -> List[sqlite3.Row]:
        return self.db.execute(
            "SELECT entity, source_id, att_id, name, size_bytes, status, reason "
            "FROM attachments WHERE status != 'done'"
        ).fetchall()

    # ------------------------------------------------------------- maintenance
    def clear_failed(self, entity: Optional[str] = None) -> int:
        """Delete idmap rows in 'failed' (and 'partial') status so a subsequent run
        retries ONLY them - everything 'done' still skips. Returns rows cleared."""
        if entity:
            cur = self.db.execute(
                "DELETE FROM idmap WHERE status IN ('failed','partial') AND entity=?",
                (entity,))
        else:
            cur = self.db.execute("DELETE FROM idmap WHERE status IN ('failed','partial')")
        self.db.commit()
        return cur.rowcount

    def remove_record(self, entity: str, source_id) -> None:
        """Forget one record (used by rollback after its target row is deleted), so a
        later run re-creates it cleanly. Also clears a ticket's conversation tracking."""
        self.db.execute("DELETE FROM idmap WHERE entity=? AND source_id=?",
                        (entity, str(source_id)))
        if entity == "ticket":
            self.db.execute("DELETE FROM conversations WHERE ticket_source_id=?",
                            (str(source_id),))
        self.db.commit()

    def reset_entity(self, entity: str) -> int:
        """Forget ALL migration state for an entity (idmap rows, and for tickets
        the conversation tracking) so it re-migrates from scratch. Target-side
        dedup still prevents duplicates via marker tags. Returns idmap rows removed."""
        cur = self.db.execute("DELETE FROM idmap WHERE entity=?", (entity,))
        if entity == "ticket":
            self.db.execute("DELETE FROM conversations")
        self.db.commit()
        return cur.rowcount
