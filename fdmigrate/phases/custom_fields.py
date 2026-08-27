"""Identify custom fields in the SOURCE and create the missing ones on the TARGET.

Freshdesk rejects custom-field VALUES for fields that don't exist on the target, so
this phase must run FIRST. Covers ticket, contact and company custom fields.
Idempotent: a field already present on the target (matched by `name`) is left alone.

After this runs, the ticket phase maps same-named fields straight through (we register
identity mappings), and contacts/companies already pass `custom_fields` as-is - so the
VALUES migrate once the field exists.
"""
from __future__ import annotations

from .base import Context

ENTITY = "custom_field"

# label, list endpoint (GET), admin create endpoint (POST)
SPECS = [
    ("ticket",  "/ticket_fields",  "/admin/ticket_fields"),
    ("contact", "/contact_fields", "/admin/contact_fields"),
    ("company", "/company_fields", "/admin/company_fields"),
]

# Field types that carry a choice list and need it sent on create.
CHOICE_TYPES = {"custom_dropdown", "nested_field", "custom_multi_select_dropdown"}

LIST_EP = {"ticket": "/ticket_fields", "contact": "/contact_fields", "company": "/company_fields"}


def build_name_map(ctx, label) -> dict:
    """Read-only: source field-name -> target field-name (match by name, else label).
    Safe to call any time (no field creation)."""
    list_ep = LIST_EP[label]
    src_fields = [f for f in (ctx.src.get(list_ep) or []) if not f.get("default")]
    tgt_fields = ctx.tgt.get(list_ep) or []
    tgt_names = {f.get("name") for f in tgt_fields}
    tgt_labels = {(f.get("label") or "").strip().lower(): f.get("name") for f in tgt_fields}
    nm = {}
    for f in src_fields:
        sn, lbl = f.get("name"), (f.get("label") or "").strip().lower()
        if sn in tgt_names:
            nm[sn] = sn
        elif lbl in tgt_labels:
            nm[sn] = tgt_labels[lbl]
    return nm


def ensure_cf_map(ctx, label) -> dict:
    """Make sure cfg.cf_name_map[label] is populated even when the custom_fields
    phase didn't run this invocation (e.g. `--only tickets` batch runs). Builds it
    read-only if empty. For tickets, also seeds cfg.map_custom_fields."""
    if not ctx.cfg.cf_name_map.get(label):
        try:
            ctx.cfg.cf_name_map[label] = build_name_map(ctx, label)
        except Exception as e:
            ctx.logger.warning(f"  could not build {label} custom-field map: {e}")
            ctx.cfg.cf_name_map[label] = {}
    if label == "ticket" and not ctx.cfg.map_custom_fields:
        ctx.cfg.map_custom_fields = dict(ctx.cfg.cf_name_map.get("ticket", {}))
    return ctx.cfg.cf_name_map[label]


def _normalize_choices(src_field) -> list | None:
    """Turn whatever GET returned into Freshdesk's create format:
    a list of {"value": <str>, "position": <int>}."""
    ch = src_field.get("choices")
    values = []
    if isinstance(ch, dict):
        values = list(ch.keys())
    elif isinstance(ch, list):
        for c in ch:
            if isinstance(c, dict):
                values.append(str(c.get("value") or c.get("label") or c.get("name")))
            elif isinstance(c, (list, tuple)):
                values.append(str(c[0]))
            else:
                values.append(str(c))
    if not values:
        return None
    return [{"value": v, "position": i + 1} for i, v in enumerate(values)]


def _create_payload(label, f, position) -> dict:
    """Build the create payload. Company fields reject the customer-facing keys
    (label_for_customers / customers_can_edit / displayed_to_customers), so they
    get a minimal payload; ticket/contact fields accept them."""
    p = {"label": f.get("label") or f.get("name"), "type": f.get("type")}
    if label == "ticket":
        p["label_for_customers"] = f.get("label_for_customers") or f.get("label") or f.get("name")
        p["customers_can_edit"] = bool(f.get("customers_can_edit", False))
        p["displayed_to_customers"] = bool(f.get("displayed_to_customers", False))
        p["required_for_closure"] = bool(f.get("required_for_closure", False))
        if position is not None:
            p["position"] = position
    elif label == "contact":
        # Contact fields accept label_for_customers but REJECT displayed_to_customers.
        p["label_for_customers"] = f.get("label_for_customers") or f.get("label") or f.get("name")
    # company: minimal payload only (rejects all the customer-facing keys)
    if f.get("type") in CHOICE_TYPES:
        choices = _normalize_choices(f)
        if choices is not None:
            p["choices"] = choices
    return p


def _create_field(ctx, admin_ep, payload):
    """Create a field; if the target rejects the position, retry letting it default."""
    r = ctx.tgt.post(admin_ep, json=payload)
    if r.status_code not in (200, 201) and "position" in (r.text or "").lower() and "position" in payload:
        payload = {k: v for k, v in payload.items() if k != "position"}
        r = ctx.tgt.post(admin_ep, json=payload)
    return r


def run(ctx: Context) -> dict:
    ctx.logger.info("=" * 70)
    ctx.logger.info("PHASE: Custom fields (identify in source -> create on target)")
    totals = {}

    for label, list_ep, admin_ep in SPECS:
        try:
            src_fields = [f for f in (ctx.src.get(list_ep) or []) if not f.get("default")]
            tgt_fields = ctx.tgt.get(list_ep) or []
        except Exception as e:
            ctx.logger.warning(f"  {label}: cannot read fields ({e}); skipping")
            continue

        tgt_names = {f.get("name") for f in tgt_fields}
        tgt_labels = {(f.get("label") or "").strip().lower(): f.get("name") for f in tgt_fields}
        next_pos = max([f.get("position") or 0 for f in tgt_fields] + [0]) + 1
        created = exists = failed = 0

        for f in src_fields:
            lbl = (f.get("label") or "").strip().lower()
            # Already on target? Match by NAME or by LABEL (create derives a new name
            # from the label, so names diverge).
            if f.get("name") in tgt_names or lbl in tgt_labels:
                exists += 1
                continue
            pos = min(next_pos, 15) if label == "ticket" else next_pos
            resp = _create_field(ctx, admin_ep, _create_payload(label, f, pos))
            next_pos += 1
            if resp.status_code in (200, 201):
                created += 1
                ctx.log(ENTITY, f.get("name"), "INFO", "field_created",
                        f"{label}: '{f.get('label')}' ({f.get('type')})")
            else:
                failed += 1
                ctx.log(ENTITY, f.get("name"), "WARNING", "field_create_failed",
                        f"{label}: {resp.status_code} {resp.text[:200]}")

        # Build source-name -> target-name map so custom-field VALUES are remapped.
        name_map = build_name_map(ctx, label)
        ctx.cfg.cf_name_map[label] = name_map

        ctx.logger.info(f"  {label}: created={created} already_exist={exists} "
                        f"failed={failed} | value-map {len(name_map)} field(s)")
        totals[label] = {"created": created, "exists": exists, "failed": failed}

    # Tickets map custom fields via cfg.map_custom_fields (strict drops unknowns).
    ctx.cfg.map_custom_fields = dict(ctx.cfg.cf_name_map.get("ticket", {}))
    return totals
