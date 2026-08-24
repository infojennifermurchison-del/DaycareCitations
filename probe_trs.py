"""One-off probe (runs on the GitHub Actions runner, which has open internet).

Goal: find out where Texas Rising Star (TRS) participation + star rating live,
and clear up the operation-number vs operation-id mismatch, so we can load TRS
onto every contact and run a weekly "which TRS providers were cited" scan.
"""
import json
import requests

BASE = "https://data.texas.gov/resource/{}.json"
OPERATIONS_ID = "bc5r-88dy"
NONCOMPLIANCE_ID = "tqgd-mf4x"
KW = ("rising", "star", "trs", "quality", "designation", "certif", "subsid")

s = requests.Session()


def get(dsid, params):
    r = s.get(BASE.format(dsid), params=params, timeout=120)
    r.raise_for_status()
    return r.json()


def dump_cols(title, cols):
    print(f"\n=== {title} columns ({len(cols)}) ===")
    for c in sorted(cols):
        print("   ", c)
    hits = [c for c in cols if any(k in c.lower() for k in KW)]
    print("  >>> TRS/quality-related columns:", hits or "NONE")


# 1) Operations dataset: columns + a full sample row
op_sample = get(OPERATIONS_ID, {"$limit": 1})
op_cols = list(op_sample[0].keys()) if op_sample else []
dump_cols("OPERATIONS (bc5r-88dy)", op_cols)
print("\n--- full sample OPERATIONS row ---")
print(json.dumps(op_sample[0], indent=2) if op_sample else "EMPTY")

# 2) Look up known operations to inspect id keys + any TRS values
for name in ("ABC Word Academy", "Kreative Dreams Academy", "Jasper High School PELE"):
    print(f"\n=== operations LIKE '{name}' ===")
    try:
        rows = get(OPERATIONS_ID, {
            "$where": f"upper(operation_name) like upper('%{name}%')",
            "$limit": 5,
        })
    except Exception as e:
        print("  query error:", e)
        rows = []
    if not rows:
        print("  (no matches)")
    for r in rows:
        idish = {k: v for k, v in r.items()
                 if "operation" in k.lower() or "number" in k.lower() or k.lower() == "id"}
        trsish = {k: v for k, v in r.items() if any(kk in k.lower() for kk in KW)}
        print("  name:", r.get("operation_name"), "| type:",
              r.get("operation_type") or r.get("type"), "| city:", r.get("city"))
        print("    id fields :", json.dumps(idish))
        print("    trs fields:", json.dumps(trsish) if trsish else "(none)")

# 3) Non-compliance dataset id keys (to understand the join)
nc_sample = get(NONCOMPLIANCE_ID, {"$limit": 1})
dump_cols("NON-COMPLIANCE (tqgd-mf4x)", list(nc_sample[0].keys()) if nc_sample else [])
