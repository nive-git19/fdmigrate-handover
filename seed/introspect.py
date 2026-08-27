"""Introspect both new instances: verify access, then compare ticket fields
(esp. CUSTOM fields) source->target, and list agents + auto-match by email.
This is the pre-migration safety check: do custom fields line up before we run?
"""
from fdmigrate.config import load_config
from fdmigrate.logs import setup_logging
from fdmigrate.runner import build_clients

cfg = load_config("config.yaml")
log = setup_logging("logs/_introspect.log")
src, tgt = build_clients(cfg, log)


def whoami(c, label):
    try:
        me = c.whoami()
        who = (me.get("contact") or {}).get("email") or me.get("id")
        print(f"[{label}] OK - {c.base_url}  (auth as {who})")
        return True
    except Exception as e:
        print(f"[{label}] FAILED: {e}")
        return False


print("=== CONNECTION CHECK ===")
ok = whoami(src, "source") & whoami(tgt, "target")
if not ok:
    raise SystemExit("connection failed - fix keys/domains before continuing")


def get_fields(c):
    out = {}
    for f in c.get("/ticket_fields") or []:
        ch = f.get("choices")
        if isinstance(ch, dict):
            choices = list(ch.keys())
        elif isinstance(ch, list):
            choices = [x[0] if isinstance(x, (list, tuple)) else x for x in ch]
        else:
            choices = None
        out[f.get("name")] = {"label": f.get("label"), "type": f.get("type"),
                              "default": bool(f.get("default")), "choices": choices}
    return out


sf, tf = get_fields(src), get_fields(tgt)

def dump(title, fields):
    print(f"\n=== {title} ticket fields ({len(fields)}) ===")
    for n, d in fields.items():
        kind = "DEF" if d["default"] else "CF "
        print(f"  {kind} {n:32} {str(d['type'])[:22]:22} choices={d['choices']}")

dump("SOURCE", sf)
dump("TARGET", tf)

scf = {n: d for n, d in sf.items() if not d["default"]}
tcf = {n: d for n, d in tf.items() if not d["default"]}

print(f"\n=== CUSTOM FIELD MATCH (source {len(scf)} -> target {len(tcf)}) ===")
problems = 0
for n, d in scf.items():
    if n in tcf:
        notes = []
        if d["type"] != tcf[n]["type"]:
            notes.append(f"TYPE DIFF src={d['type']} tgt={tcf[n]['type']}"); problems += 1
        if d["choices"] and tcf[n]["choices"] is not None:
            missing = [c for c in d["choices"] if c not in tcf[n]["choices"]]
            if missing:
                notes.append(f"choices MISSING in target: {missing}"); problems += 1
        print(f"  {'OK  ' if not notes else 'WARN'} {n}: {'; '.join(notes) if notes else 'matches'}")
    else:
        print(f"  MISS {n} (type={d['type']}) -> NOT in target; values would be DROPPED"); problems += 1
extra = [n for n in tcf if n not in scf]
if extra:
    print(f"  (target-only custom fields, harmless: {extra})")

print("\n=== AGENTS ===")
def emails(c):
    return sorted({((a.get("contact") or {}).get("email") or a.get("email") or "").lower()
                   for a in c.paginate("/agents")})
sa, ta = set(emails(src)), set(emails(tgt))
print(f"source agents: {sorted(sa)}")
print(f"target agents: {sorted(ta)}")
print(f"auto-match (same email): {sorted(sa & ta)}")
print(f"source agents NOT in target (need mapping or recreate): {sorted(sa - ta)}")

print(f"\n=== VERDICT: custom-field problems = {problems} ===")
print("0 = every source custom field has a matching target field; safe to migrate values."
      if problems == 0 else "Fix the WARN/MISS items above (create/align fields in target) before migrating.")
