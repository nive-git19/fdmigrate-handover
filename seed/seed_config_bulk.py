"""Phase A - build the config the 1,000-ticket dataset hangs off.

Idempotent: everything is matched by name first, so a re-run after a 429 or a
Ctrl-C adds nothing twice. Nothing here is destructive - no deletes, and no
edits to objects it did not create.

    FD_SOURCE_DOMAIN=... FD_SOURCE_API_KEY=... py seed_config_bulk.py
"""
import random
from pathlib import Path
from fdapi import client_from_env, FDError

random.seed(20260831)
HERE = Path(__file__).parent
c = client_from_env("SOURCE")

GROUPS = ["Clinical Support", "Logistics & Delivery",
          "Technical Support", "Complaints & Quality"]

FIELDS = [
    # migration-critical: these are what make the platform limits survivable
    ("Original Created Date",   "custom_date",      None),
    ("Original Ticket ID",      "custom_number",    None),
    ("Original Requester",      "custom_text",      None),
    ("Original Agent",          "custom_text",      None),
    # business fields, so custom-field VALUE migration is exercised for real
    ("Care Category",           "custom_dropdown",
     ["Clinical", "Billing", "Technical", "Logistics", "Complaint", "General"]),
    ("Region",                  "custom_dropdown",
     ["Finland", "Sweden", "Norway", "Denmark", "Other"]),
    ("Escalated to Clinical",   "custom_checkbox",  None),
    ("Order Reference",         "custom_text",      None),
]

COMPANIES = [
    "Helsinki Care Clinic", "Espoo Family Health", "Tampere Wellness Oy",
    "Turku Medical Group", "Oulu Senior Care", "Vantaa Home Nursing",
    "Jyvaskyla Rehab", "Kuopio Health Partners", "Lahti Care Services",
    "Stockholm Vardcentral", "Goteborg Halsa AB", "Malmo Omsorg",
    "Oslo Helsehus", "Bergen Omsorg AS", "Kobenhavn Sundhed",
    "Aarhus Plejecenter",
]

FIRST = ["Aino", "Eero", "Helmi", "Vaino", "Sofia", "Onni", "Aada", "Elias",
         "Venla", "Leevi", "Emma", "Niilo", "Iida", "Oliver", "Lilja",
         "Kasper", "Ella", "Aarne", "Nella", "Anni", "Joonas", "Siiri",
         "Matias", "Vilma", "Rasmus", "Kerttu", "Eemil", "Peppi", "Toivo",
         "Astrid", "Lars", "Ingrid", "Bjorn", "Freja", "Mikkel", "Saga",
         "Anders", "Linnea", "Henrik", "Maarit"]
LAST = ["Virtanen", "Korhonen", "Makinen", "Nieminen", "Makela", "Hamalainen",
        "Laine", "Heikkinen", "Koskinen", "Jarvinen", "Lehtonen", "Lehtinen",
        "Saarinen", "Salminen", "Heinonen", "Niemi", "Heikkila", "Kinnunen",
        "Salonen", "Turunen", "Andersson", "Johansson", "Karlsson", "Nilsen",
        "Hansen", "Pedersen", "Larsen", "Olsen", "Berg", "Lindqvist"]

TARGET_CONTACTS = 200


def named(path, key="name"):
    return {x[key]: x["id"] for x in c.paginate(path)}


def step(label, fn):
    print("\n== " + label, flush=True)
    try:
        fn()
    except FDError as e:
        print("   FAILED " + str(e), flush=True)


def do_groups():
    have = named("/groups")
    for g in GROUPS:
        if g in have:
            print("   exists  " + g)
            continue
        r = c.post("/groups", {"name": g,
                               "description": g + " queue - bulk seed"})
        print("   created {}  id={}".format(g, r["id"]))


def do_fields():
    have = {f["label"]: f for f in c.get("/ticket_fields")}
    for label, ftype, choices in FIELDS:
        if label in have:
            print("   exists  " + label)
            continue
        body = {"label": label, "type": ftype,
                "label_for_customers": label, "customers_can_edit": False,
                "displayed_to_customers": False, "required_for_closure": False}
        if choices:
            body["choices"] = [{"value": v, "position": i + 1}
                               for i, v in enumerate(choices)]
        # The documented write endpoint is /admin/ticket_fields; some stacks
        # still accept the bare path. Try both before calling it a failure.
        for path in ("/admin/ticket_fields", "/ticket_fields"):
            try:
                r = c.post(path, body)
                print("   created {:<26} {}  -> {}".format(
                    label, ftype, r.get("name")))
                break
            except FDError as e:
                if path == "/ticket_fields":
                    print("   FAILED  {}: {} {}".format(
                        label, e.code, e.body[:160]))


def do_companies():
    have = named("/companies")
    for co in COMPANIES:
        if co in have:
            print("   exists  " + co)
            continue
        c.post("/companies", {"name": co,
                              "description": "Bulk seed account",
                              "note": "seeded for migration rehearsal"})
        print("   created " + co)


def do_contacts():
    existing = c.paginate("/contacts")
    companies = list(named("/companies").values())
    print("   have {} contacts, target {}".format(
        len(existing), TARGET_CONTACTS))
    made = 0
    n = len(existing)
    seen = {x.get("email") for x in existing}
    i = 0
    while n < TARGET_CONTACTS and i < 900:
        i += 1
        fn, ln = random.choice(FIRST), random.choice(LAST)
        email = "{}.{}{}@evercare-test.example".format(
            fn.lower(), ln.lower(), i)
        if email in seen:
            continue
        try:
            c.post("/contacts", {
                "name": fn + " " + ln,
                "email": email,
                "company_id": random.choice(companies) if companies else None,
                "unique_external_id": "bulk-{:04d}".format(i),
                "tags": ["zzz-bulk-contact"]})
            seen.add(email)
            n += 1
            made += 1
            if made % 25 == 0:
                print("   ... {}/{}".format(n, TARGET_CONTACTS), flush=True)
        except FDError as e:
            if e.code != 409:
                print("   contact {}: {} {}".format(email, e.code, e.body[:120]))
    print("   created {}, now {}".format(made, n))


def do_sla():
    """The Default policy cannot be edited via the API (400
    cannot_update_default_sla), so SLA behaviour can only be varied by adding
    scoped policies that sit above it."""
    groups = named("/groups")
    have = {p["name"] for p in c.get("/sla_policies")}
    wanted = [
        ("Clinical - fast response",
         {"group_ids": [groups[g] for g in ("Clinical Support", "Escalations")
                        if g in groups]},
         {"priority_4": dict(respond_within=600, resolve_within=7200,
                             business_hours=False, escalation_enabled=True),
          "priority_3": dict(respond_within=1800, resolve_within=14400,
                             business_hours=False, escalation_enabled=True),
          "priority_2": dict(respond_within=3600, resolve_within=28800,
                             business_hours=True, escalation_enabled=True),
          "priority_1": dict(respond_within=7200, resolve_within=57600,
                             business_hours=True, escalation_enabled=False)}),
        ("Logistics - business hours only",
         {"group_ids": [groups[g] for g in ("Logistics & Delivery",)
                        if g in groups]},
         {"priority_4": dict(respond_within=3600, resolve_within=28800,
                             business_hours=True, escalation_enabled=True),
          "priority_3": dict(respond_within=7200, resolve_within=57600,
                             business_hours=True, escalation_enabled=False),
          "priority_2": dict(respond_within=14400, resolve_within=86400,
                             business_hours=True, escalation_enabled=False),
          "priority_1": dict(respond_within=28800, resolve_within=172800,
                             business_hours=True, escalation_enabled=False)}),
    ]
    for name, applicable, targets in wanted:
        if name in have:
            print("   exists  " + name)
            continue
        try:
            r = c.post("/sla_policies", {"name": name,
                                         "description": "bulk seed",
                                         "sla_target": targets,
                                         "applicable_to": applicable})
            print("   created {}  id={}".format(name, r["id"]))
        except FDError as e:
            print("   FAILED  {}: {} {}".format(name, e.code, e.body[:220]))


step("groups", do_groups)
step("ticket fields", do_fields)
step("companies", do_companies)
step("contacts", do_contacts)
step("sla policies", do_sla)

print("\nDONE - {} API calls, {} throttle waits".format(c.calls, c.throttled))
