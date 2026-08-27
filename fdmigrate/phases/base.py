"""Shared context and helpers for all phases."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict

from ..client import FreshdeskClient
from ..config import Config
from ..store import Store


@dataclass
class Context:
    src: FreshdeskClient      # source account - READ ONLY
    tgt: FreshdeskClient      # target account - the only one written to
    store: Store
    cfg: Config
    logger: logging.Logger
    counts: Dict[str, int] = field(default_factory=dict)

    def bump(self, key: str, n: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + n

    def log(self, entity, source_id, level, action, message) -> None:
        self.store.log_event(entity, source_id, level, action, message)
        if level in ("WARNING", "ERROR"):
            self.logger.warning(f"[{entity}:{source_id}] {action}: {message}")


class Heartbeat:
    """Time-based progress reporter: logs a single informative line every
    `every` seconds (with throughput + elapsed) instead of one line per record,
    so a 52,500-ticket run shows steady progress without flooding the output."""

    def __init__(self, logger: logging.Logger, label: str, every: int = 20):
        self.logger = logger
        self.label = label
        self.every = every
        self.t0 = time.time()
        self.last = self.t0

    def beat(self, processed: int, force: bool = False, total: int | None = None,
             **counts) -> None:
        now = time.time()
        if not force and now - self.last < self.every:
            return
        self.last = now
        elapsed = now - self.t0
        rate = processed / elapsed * 60 if elapsed > 0 else 0
        extra = " ".join(f"{k}={v}" for k, v in counts.items())
        eta = ""
        if total and rate > 0 and processed < total:
            eta = f" | ETA ~{(total - processed) / (rate / 60) / 60:.0f}m ({processed}/{total})"
        self.logger.info(f"  {self.label}: {processed} processed | {extra} | "
                         f"{rate:.0f}/min | elapsed {elapsed:.0f}s{eta}")


def strip_empty(d: dict) -> dict:
    """Drop keys whose value is None / '' / [] - Freshdesk rejects some nulls."""
    return {k: v for k, v in d.items() if v not in (None, "", [])}


def norm(s: str | None) -> str:
    return (s or "").strip().lower()


def remap_cf(values: dict | None, name_map: dict | None) -> dict:
    """Remap custom-field VALUE keys from source field-names to target field-names.
    Drops any key with no target field (a single unknown key makes Freshdesk reject
    the ENTIRE record). With an empty map, returns {} (safer than passing unknowns)."""
    if not values:
        return {}
    nm = name_map or {}
    # Drop null values: Freshdesk rejects a null for a typed custom field.
    return {nm[k]: v for k, v in values.items() if k in nm and v is not None}


def provenance_banner(t: dict) -> str:
    """Freshdesk rejects back-dated created_at/updated_at on tickets and notes,
    so original timestamps are preserved as a small HTML banner instead."""
    parts = []
    if t.get("created_at"):
        parts.append(f"Originally created: {t['created_at']}")
    if t.get("updated_at"):
        parts.append(f"Last updated: {t['updated_at']}")
    if t.get("status"):
        parts.append(f"Original status code: {t['status']}")
    if not parts:
        return ""
    return ("<hr><p style='color:#888;font-size:12px'>[Migrated record] "
            + " &middot; ".join(parts) + "</p>")


def note_provenance_line(conv: dict, author_hint: str = "") -> str:
    bits = []
    if conv.get("created_at"):
        bits.append(f"Originally posted: {conv['created_at']}")
    if author_hint:
        bits.append(f"Author: {author_hint}")
    if not bits:
        return ""
    return f"<p style='color:#888;font-size:12px'>[Migrated] {' &middot; '.join(bits)}</p>"
