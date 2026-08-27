"""Config loading + validation. Secrets come from env vars, never hard-coded."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import yaml

# Env vars that override api_key regardless of what's in the file.
ENV_SOURCE_KEY = "FD_SOURCE_API_KEY"
ENV_TARGET_KEY = "FD_TARGET_API_KEY"

DEFAULT_OBJECTS = {
    "custom_fields": True,
    "agents": True,
    "companies": True,
    "contacts": True,
    "groups": True,
    "tickets": True,
    "canned_responses": True,
    "knowledge_base": True,
}

# Dependency-correct order. Companies/contacts before tickets; agents before
# group membership and ticket assignment; canned/kb last (independent).
PHASE_ORDER = [
    "custom_fields",
    "agents",
    "companies",
    "contacts",
    "groups",
    "tickets",
    "canned_responses",
    "knowledge_base",
]


def _resolve_key(raw: str | None, env_override: str) -> str:
    """Resolve an api_key value. Priority: env override > 'env:NAME' ref > literal.
    Returns "" if an env ref is unset - a missing key is reported by validate(),
    so local-only commands (status/reset) can load the config without credentials."""
    if os.getenv(env_override):
        return os.environ[env_override]
    if raw and raw.startswith("env:"):
        return os.getenv(raw.split(":", 1)[1]) or ""
    return raw or ""


class ConfigError(Exception):
    pass


class Config:
    def __init__(self, data: Dict[str, Any]):
        self.raw = data

        src = data.get("source") or {}
        tgt = data.get("target") or {}
        self.source_domain = (src.get("domain") or "").rstrip("/")
        self.target_domain = (tgt.get("domain") or "").rstrip("/")
        self.source_key = _resolve_key(src.get("api_key"), ENV_SOURCE_KEY)
        self.target_key = _resolve_key(tgt.get("api_key"), ENV_TARGET_KEY)

        self.objects = {**DEFAULT_OBJECTS, **(data.get("objects") or {})}

        t = data.get("tickets") or {}
        self.tickets_updated_since = t.get("updated_since", "2010-01-01T00:00:00Z")
        self.ticket_limit = t.get("ticket_limit")
        self.include_spam = bool(t.get("include_spam", False))
        self.include_conversations = bool(t.get("include_conversations", True))
        self.include_attachments = bool(t.get("include_attachments", True))
        # Stamp a hidden marker tag on each migrated ticket and, before creating,
        # check the target for it - so re-runs (or a target already populated by
        # another run/tool) never create duplicate tickets.
        self.tickets_check_target_markers = bool(t.get("check_target_markers", True))
        # DEFAULT: replicate conversations AS-IS - customer replies stay incoming
        # customer messages, agent replies post as real replies (/reply), private
        # notes stay private notes. PRE-REQUISITE: disable ALL notifications and
        # automations on the target first, or replies will email real customers.
        # Set conversations_as_notes: true for the legacy safe mode (everything
        # becomes a note; notes never email) when notifications can't be disabled.
        self.conversations_as_notes = bool(t.get("conversations_as_notes", False))
        # Legacy notes-mode option: make every migrated note public. No effect in
        # the default as-is mode (visibility always mirrors the source there).
        self.force_public_notes = bool(t.get("force_public_notes", False))
        # Optionally append the original-timestamp banner to descriptions/notes.
        # OFF by default: a migrated ticket carries ONLY its marker tag, no
        # injected content.
        self.provenance_banner = bool(t.get("provenance_banner", False))
        # Optional match-wise filtering: migrate only tickets matching ALL of the
        # given criteria (a missing/empty key = no constraint on that dimension).
        # Lets you migrate in verifiable batches (e.g. one group/status at a time)
        # or replicate a Freshdesk "view" (which has no API of its own).
        self.ticket_filters = t.get("filters") or {}
        # Optional: also stamp the original created_at into this TARGET custom
        # field (by its Freshdesk field NAME, e.g. cf_original_created_date).
        # The field must exist on the target as a text field. With the banner
        # off (default) this is the ONLY place the original date is preserved.
        self.created_date_custom_field = t.get("created_date_custom_field")
        # Set by the --delta CLI flag: re-sync conversations/status of already-
        # migrated tickets whose source updated_at is newer than the last run.
        self.delta = False
        # Set by the --dry-run CLI flag: reads stay real, but every TARGET write is
        # intercepted and logged instead of sent - validate a client config end-to-
        # end with zero writes. Use a throwaway --state-dir (the idmap fills with
        # synthetic negative ids).
        self.dry_run = False
        # ARCHIVED TICKETS (D2). Freshdesk removes archived (old/closed) tickets
        # from the LIST endpoint entirely - the normal run and the completeness
        # check both enumerate via that list, so archived tickets are invisible to
        # both. There is NO archived-list API: only per-id GET /tickets/archived/{id}.
        # Enable this to sweep an id range and ingest any archived tickets found.
        # COST: one GET per candidate id, so it can be many calls on a large, old
        # account - scope it with archived_id_min/max. Run it AFTER a full (un-
        # limited) live pass so live ids are already 'done' and skipped.
        self.archived_scan = bool(t.get("archived_scan", False))
        self.archived_id_min = t.get("archived_id_min")   # default 1
        self.archived_id_max = t.get("archived_id_max")   # default: max known live id

        a = data.get("attachments") or {}
        self.attachment_max_mb = float(a.get("max_mb", 20))
        self.attachment_tmp_dir = a.get("tmp_dir", "./_attachments_tmp")

        c = data.get("contacts") or {}
        self.contacts_update_on_match = bool(c.get("update_on_match", True))
        self.contacts_updated_since = c.get("updated_since", "2010-01-01T00:00:00Z")

        m = data.get("mapping") or {}
        self.map_agents = {str(k).lower(): str(v).lower() for k, v in (m.get("agents") or {}).items()}
        self.map_custom_fields = m.get("custom_fields") or {}
        # Populated by the custom_fields phase: source field-name -> target field-name
        # per entity (Freshdesk derives a NEW name from the label on create, so names
        # diverge). Used to remap custom-field VALUES so they aren't rejected.
        self.cf_name_map = {"ticket": {}, "contact": {}, "company": {}}
        self.custom_field_strict = bool(m.get("custom_field_strict", True))
        self.map_status = {int(k): int(v) for k, v in (m.get("status") or {}).items()}
        self.map_priority = {int(k): int(v) for k, v in (m.get("priority") or {}).items()}
        self.map_source = {int(k): int(v) for k, v in (m.get("source") or {}).items()}

        r = data.get("run") or {}
        self.save_every = int(r.get("save_every", 20))
        self.rate_limit_floor = int(r.get("rate_limit_floor", 3))
        self.report_path = r.get("report_path", "./reports/reconciliation.csv")
        self.manifest_path = r.get("manifest_path", "./reports/attachment_manifest.csv")
        self.failures_path = r.get("failures_path", "./reports/failures.csv")
        self.spot_check_path = r.get("spot_check_path", "./reports/ticket_spot_check.csv")
        self.spot_check_sample = int(r.get("spot_check_sample", 25))

    def validate(self) -> None:
        problems = []
        if not self.source_domain.startswith("http"):
            problems.append("source.domain must be a full https URL")
        if not self.target_domain.startswith("http"):
            problems.append("target.domain must be a full https URL")
        if not self.source_key:
            problems.append(f"missing source api key (set {ENV_SOURCE_KEY} or source.api_key)")
        if not self.target_key:
            problems.append(f"missing target api key (set {ENV_TARGET_KEY} or target.api_key)")
        if self.source_domain and self.source_domain == self.target_domain:
            problems.append("source and target domains are identical - refusing to migrate onto itself")
        if problems:
            raise ConfigError("Invalid config:\n  - " + "\n  - ".join(problems))

    def enabled_phases(self) -> list[str]:
        return [p for p in PHASE_ORDER if self.objects.get(p)]


def load_config(path: str | Path, validate: bool = True) -> Config:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"config file not found: {p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    cfg = Config(data)
    if validate:  # local-only commands (status/reset) pass validate=False
        cfg.validate()
    return cfg
