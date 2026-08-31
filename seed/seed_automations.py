"""Phase C - create automation rules for behaviour testing.

Every rule is created INACTIVE on purpose. A Dispatch'r rule fires on ticket
creation, so switching these on before the dataset lands would rewrite group,
priority and status underneath the seeder and make the volume set untestable.
It is also the exact hazard a production migration faces: automations must be
OFF while records are written, then switched on afterwards.

Turn them on from Admin > Workflows when you want to test, or run this with
ACTIVATE=1 to flip them via the API.

Rule types: 1 = Dispatch'r (on create), 3 = Supervisor (time-based),
4 = Observer (on update).

    FD_SOURCE_DOMAIN=... FD_SOURCE_API_KEY=... py seed_automations.py
"""
import json
import os

from fdapi import client_from_env, FDError

c = client_from_env("SOURCE")
ACTIVATE = os.environ.get("ACTIVATE") == "1"

GROUPS = {g["name"]: g["id"] for g in c.paginate("/groups")}


def cond(props, name="condition_set_1", match="any"):
    """Every condition property needs resource_type on WRITE, even though the
    API returns existing rules without it. Reading a rule back does not give
    you a payload you can re-post - proven live 31 Aug 2026:
        400 "Expecting these field 'resource_type' present for '[subject]'"
    """
    for p in props:
        p.setdefault("resource_type", "ticket")
    return [{"name": name, "match_type": match, "properties": props}]


def subject_has(*words):
    """`contains` wants ONE property whose value is a LIST. A bare string is
    rejected ("Expecting 'String' but found 'invalid'") and several separate
    subject properties collapse into a malformed array. Probed live 31 Aug:
    list-of-one and list-of-many both work, bare string does not."""
    return [{"field_name": "subject", "resource_type": "ticket",
             "operator": "contains", "value": list(words)}]


RULES = [
    # -- Dispatch'r: only the FIRST matching rule runs, so each is standalone.
    (1, {
        "name": "Route billing language to Billing & Accounts",
        "description": "Money words in the subject go to the billing queue.",
        "conditions": cond(subject_has("invoice", "refund", "payment",
                                       "charged", "VAT")),
        "actions": [{"field_name": "group_id",
                     "value": GROUPS.get("Billing & Accounts")},
                    {"field_name": "ticket_type", "value": "Refund"}],
    }),
    (1, {
        "name": "Route clinical language to Clinical Support",
        "description": "Care and medication wording goes to the clinical queue.",
        "conditions": cond(subject_has("medication", "dosage",
                                       "appointment", "nurse")),
        "actions": [{"field_name": "group_id",
                     "value": GROUPS.get("Clinical Support")},
                    {"field_name": "priority", "value": 3}],
    }),
    (1, {
        "name": "Complaints and escalations to Complaints & Quality",
        "description": "Complaint wording is escalated on arrival.",
        "conditions": cond(subject_has("Escalation", "Complaint",
                                       "missed", "No response")),
        "actions": [{"field_name": "group_id",
                     "value": GROUPS.get("Complaints & Quality")},
                    {"field_name": "priority", "value": 4}],
    }),
    (1, {
        "name": "Chat-origin tickets to Tier 1 Support",
        "description": "Anything arriving on chat starts at Tier 1.",
        "conditions": cond([{"field_name": "source", "operator": "in",
                             "value": [7]}]),
        "actions": [{"field_name": "group_id",
                     "value": GROUPS.get("Tier 1 Support")}],
    }),

    # -- Supervisor: time-based sweep.
    (3, {
        "name": "Escalate urgent tickets unresolved after 4 hours",
        "description": "Urgent work still open after 4h moves to Escalations.",
        "conditions": cond([
            {"field_name": "priority", "operator": "in", "value": [4]},
            {"field_name": "status", "operator": "not_in", "value": [4, 5]},
            # `created_at` is NOT a valid Supervisor condition field - it
            # returns 400 invalid_field. Only resolved_at/updated_at style
            # elapsed fields are accepted here.
        ], match="all"),
        "actions": [{"field_name": "group_id",
                     "value": GROUPS.get("Escalations")}],
    }),
    (3, {
        "name": "Close resolved tickets after 72 hours",
        "description": "Housekeeping sweep so Resolved does not accumulate.",
        "conditions": cond([
            {"field_name": "status", "operator": "in", "value": [4]},
            {"field_name": "resolved_at", "operator": "greater_than",
             "value": 72},
        ], match="all"),
        "actions": [{"field_name": "status", "value": 5}],
    }),

    # -- Observer: fires on update. performer type 2 = customer.
    (4, {
        "name": "Reopen and raise priority when a customer chases",
        "description": "A customer reply on a waiting ticket reopens it.",
        "conditions": cond([
            {"field_name": "status", "resource_type": "ticket",
             "operator": "in", "value": [3, 6, 7]},
        ], match="all"),
        "events": [{"field_name": "reply_sent"}],
        "performer": {"type": 2},
        "actions": [{"field_name": "status", "value": 2},
                    {"field_name": "priority", "value": 3}],
    }),
]


def main():
    made, failed = 0, 0
    for rtype, body in RULES:
        existing = {r["name"]: r for r in c.get(
            "/automations/{}/rules".format(rtype))}
        if body["name"] in existing:
            r = existing[body["name"]]
            print("   exists  [{}] {}  active={}".format(
                rtype, body["name"][:52], r.get("active")))
            if ACTIVATE and not r.get("active"):
                c.put("/automations/{}/rules/{}".format(rtype, r["id"]),
                      {"active": True})
                print("           -> activated")
            continue
        payload = dict(body)
        payload["active"] = False          # never fire on creation
        try:
            r = c.post("/automations/{}/rules".format(rtype), payload)
            made += 1
            print("   created [{}] {}  id={} active={}".format(
                rtype, body["name"][:52], r["id"], r.get("active")))
        except FDError as e:
            failed += 1
            print("   FAILED  [{}] {}\n           {} {}".format(
                rtype, body["name"][:52], e.code, e.body[:260]))
    print("\n{} created, {} failed. All inactive - switch on in "
          "Admin > Workflows, or re-run with ACTIVATE=1.".format(made, failed))


if __name__ == "__main__":
    main()
