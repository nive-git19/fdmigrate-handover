"""Migration phases. Each module exposes ENTITY and run(ctx) -> dict of counts."""
from . import agents, companies, contacts, groups, tickets, canned, kb, custom_fields

REGISTRY = {
    "custom_fields": custom_fields,
    "agents": agents,
    "companies": companies,
    "contacts": contacts,
    "groups": groups,
    "tickets": tickets,
    "canned_responses": canned,
    "knowledge_base": kb,
}
