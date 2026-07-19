"""Texas Child Care Regulation (CCR) open-data access.

Pulls training-related citations for daycares from the Texas HHS "Search Texas
Child Care" open datasets on data.texas.gov and returns them as a tidy list of
dicts. Shared by the Colab notebook and by the weekly GoHighLevel agent.

Datasets joined (citation, its date, and the facility live in separate tables):
  * Inspection/Investigation  m5q4-3y3d  -> WHEN  (activity_date), key activity_id
  * Non-Compliance            tqgd-mf4x  -> WHAT  (standard_number_description)
  * Operations                bc5r-88dy  -> WHO   (name/address/city/phone/email)
"""

import re
import datetime as dt
import requests
import pandas as pd

NONCOMPLIANCE_ID = "tqgd-mf4x"
INSPECTIONS_ID   = "m5q4-3y3d"
OPERATIONS_ID    = "bc5r-88dy"
BASE = "https://data.texas.gov/resource/{}.json"

# Training standards: Chapter 746 (centers) / 747 (homes) professional-development
# range, matched by standard number OR keyword so wording changes don't break it.
TRAINING_STANDARD_REGEX = re.compile(r"\b7(?:46|47)\.13\d\d", re.I)
TRAINING_KEYWORDS = [
    "training hour", "clock hour", "annual training", "hours of training",
    "pre-service training", "preservice training", "professional development",
    "orientation", "training clock",
]
HOURS_KEYWORDS = ["training hour", "clock hour", "annual training", "hours of training"]


# ---------------------------------------------------------------------------
# SODA helpers
# ---------------------------------------------------------------------------
def _session(app_token=""):
    s = requests.Session()
    if app_token:
        s.headers["X-App-Token"] = app_token
    return s


def _get(session, dataset_id, params):
    r = session.get(BASE.format(dataset_id), params=params, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"SODA {dataset_id} HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


def _get_all(session, dataset_id, where=None, select=None, order=None, page=50000):
    rows, offset = [], 0
    while True:
        params = {"$limit": page, "$offset": offset}
        if where:  params["$where"]  = where
        if select: params["$select"] = select
        if order:  params["$order"]  = order
        chunk = _get(session, dataset_id, params)
        rows.extend(chunk)
        if len(chunk) < page:
            break
        offset += page
    return pd.DataFrame(rows)


def _looks_like_date(value):
    if value is None:
        return False
    s = str(value)
    if re.fullmatch(r"\d+", s):          # pure integer -> an ID, not a date
        return False
    try:
        ts = pd.to_datetime(s, errors="raise")
        return 1990 <= ts.year <= 2100
    except Exception:
        return False


def _find_date_col(sample_row, prefer):
    lower = {c.lower(): c for c in sample_row}
    for p in prefer:
        if p in lower and _looks_like_date(sample_row[lower[p]]):
            return lower[p]
    for c in sample_row:
        if _looks_like_date(sample_row[c]):
            return c
    return None


def _pick(cols, prefer, keywords):
    lower = {c.lower(): c for c in cols}
    for p in prefer:
        if p in lower:
            return lower[p]
    for c in cols:
        if any(k in c.lower() for k in keywords):
            return c
    return None


def _col_is_numeric(sample_row, name):
    v = sample_row.get(name)
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _in_clause(col, values, numeric):
    if numeric:
        vals = ",".join(str(int(float(v))) for v in values)
    else:
        vals = ",".join("'" + str(v).replace("'", "''") + "'" for v in values)
    return f"{col} in ({vals})"


def _chunked(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# ---------------------------------------------------------------------------
# Classification + facility link
# ---------------------------------------------------------------------------
def classify(std, txt):
    """Bucket a citation. Specific carve-outs are tested before the annual-hours
    catch-all so 746.1305 (pre-service) isn't mislabeled as annual hours."""
    s = f"{std} {txt}".lower()
    if any(k in s for k in ("cpr", "first aid", "first-aid", "rescue breathing")):
        return "Pediatric CPR / First Aid"
    if ("pre-service" in s or "preservice" in s or "pre service" in s
            or re.search(r"7(?:46|47)\.1305", s)):
        return "Pre-service training"
    if "orientation" in s:
        return "Orientation"
    if re.search(r"7(?:46|47)\.1309|7(?:46|47)\.1311|\(a\)\(5\)", s) or \
       any(k in s for k in ("annual training", "clock hour", "hours of training",
                            "24 annual", "leadership", "management training")):
        return "Annual training hours"
    return "Other training"


def compliance_url(operation_id, operation_type=""):
    opid = re.sub(r"\.0$", "", str(operation_id))
    res = "false"
    if re.search(r"residential|general residential|gro|child-placing",
                 str(operation_type), re.I):
        res = "true"
    return ("https://childcare.hhs.texas.gov/Public/OperationDetails"
            f"?operationId={opid}&resCareFlag={res}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def fetch_training_citations(days_back=7, anchor="auto", app_token="",
                             strict_hours_only=False, verbose=True):
    """Return a DataFrame of training-related daycare citations in the window.

    Columns: operation_id, operation_name, location_address, city, county, zip,
    phone, email, activity_date, standard_number_description, standard_risk_level,
    narrative, violation_type, compliance_page.
    """
    s = _session(app_token)

    def say(*a):
        if verbose:
            print(*a)

    # 1) Inspections table -> date + ids
    insp_sample = _get(s, INSPECTIONS_ID, {"$limit": 1})
    if not insp_sample:
        raise RuntimeError("No rows from the Inspections dataset.")
    insp_cols = list(insp_sample[0].keys())
    INSP_DATE = _find_date_col(insp_sample[0],
                               ["activity_date", "inspection_date", "date"])
    INSP_ACT  = _pick(insp_cols, ["activity_id"], ["activity_id", "activity"])
    if not (INSP_DATE and INSP_ACT):
        raise RuntimeError(f"Could not map inspection columns: {insp_cols}")

    # 2) Window
    if anchor == "auto":
        newest = _get(s, INSPECTIONS_ID, {"$select": f"max({INSP_DATE}) as m"})[0]["m"]
        anchor_date = pd.to_datetime(newest).normalize()
    else:
        anchor_date = pd.Timestamp(dt.date.today())
    start = anchor_date - pd.Timedelta(days=days_back)
    start_iso = start.strftime("%Y-%m-%dT00:00:00")
    say(f"Window: {start.date()} .. {anchor_date.date()}")

    # 3) Recent activity_ids
    sel = f"{INSP_ACT},{INSP_DATE}"
    insp = _get_all(s, INSPECTIONS_ID, where=f"{INSP_DATE} >= '{start_iso}'",
                    select=sel, order=f"{INSP_DATE} DESC")
    if insp.empty:
        say("No inspection activity in window.")
        return pd.DataFrame()
    act_ids = list(insp[INSP_ACT].dropna().astype(str).unique())

    # 4) Non-Compliance rows for those activities
    nc_sample = _get(s, NONCOMPLIANCE_ID, {"$limit": 1})
    nc_cols = list(nc_sample[0].keys())
    NC_ACT = _pick(nc_cols, ["activity_id"], ["activity_id", "activity"])
    NC_OP  = _pick(nc_cols, ["operation_id", "operation_number"], ["operation"])
    NC_STD = _pick(nc_cols, ["standard_number_description", "standard_number"],
                   ["standard_number", "standard", "section"])
    NC_TXT = _pick(nc_cols, ["narrative", "standard_description"],
                   ["narrative", "description", "text"])
    NC_RISK = _pick(nc_cols, ["standard_risk_level"], ["risk"])
    NC_ACT_NUM = _col_is_numeric(nc_sample[0], NC_ACT)

    frames = []
    for batch in _chunked(act_ids, 200):
        frames.append(_get_all(s, NONCOMPLIANCE_ID,
                               where=_in_clause(NC_ACT, batch, NC_ACT_NUM)))
    nc = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if nc.empty:
        say("No deficiencies for activities in window.")
        return pd.DataFrame()

    std_num = nc[NC_STD].fillna("").astype(str) if NC_STD else pd.Series("", index=nc.index)
    std_txt = nc[NC_TXT].fillna("").astype(str) if NC_TXT else pd.Series("", index=nc.index)
    blob = std_num + " " + std_txt
    mask = std_num.str.contains(TRAINING_STANDARD_REGEX) | \
        blob.str.lower().apply(lambda x: any(k in x for k in TRAINING_KEYWORDS))
    if strict_hours_only:
        mask &= blob.str.lower().apply(lambda x: any(k in x for k in HOURS_KEYWORDS))
    training = nc[mask].copy()
    if training.empty:
        say("No training-hour citations in window.")
        return pd.DataFrame()

    # attach date
    insp_dates = insp[[INSP_ACT, INSP_DATE]].drop_duplicates(INSP_ACT)
    insp_dates[INSP_ACT] = insp_dates[INSP_ACT].astype(str)
    training[NC_ACT] = training[NC_ACT].astype(str)
    training = training.merge(insp_dates, how="left", left_on=NC_ACT, right_on=INSP_ACT)

    # 5) Operations -> facility details
    op_ids = [str(x) for x in training[NC_OP].dropna().unique()] if NC_OP else []
    if op_ids:
        # Only use the sample row to find the (never-null) join id + its type.
        # DON'T use it to choose which columns to keep: Socrata omits null fields
        # per row, so sparse columns like phone_number/email_address are missing
        # from an arbitrary sample row. We select KEEP columns from the union of
        # all fetched rows (pd.DataFrame keys) instead.
        op_sample = _get(s, OPERATIONS_ID, {"$limit": 1})
        op_cols = list(op_sample[0].keys()) if op_sample else []
        OP_JOIN = _pick(op_cols, ["operation_id", "operation_number"], ["operation"])
        OP_JOIN_NUM = _col_is_numeric(op_sample[0], OP_JOIN) if op_sample else True
        frames = []
        for batch in _chunked(op_ids, 200):
            frames.append(_get_all(s, OPERATIONS_ID,
                                   where=_in_clause(OP_JOIN, batch, OP_JOIN_NUM)))
        ops = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if not ops.empty:
            TARGET = ("operation_name", "operation_type", "type",
                      "location_address", "address", "mailing_address",
                      "city", "county", "zip", "phone", "phone_number",
                      "email_address", "email", "website_address",
                      "administrator_director_name", "director_name")
            keep = list(dict.fromkeys(
                [OP_JOIN] + [c for c in ops.columns if c.lower() in TARGET]))
            ops = ops[keep].drop_duplicates(OP_JOIN)
            ops[OP_JOIN] = ops[OP_JOIN].astype(str)
            training[NC_OP] = training[NC_OP].astype(str)
            training = training.merge(ops, how="left", left_on=NC_OP,
                                      right_on=OP_JOIN, suffixes=("", "_op"))

    # violation_type + compliance link
    _s = training[NC_STD].fillna("").astype(str) if NC_STD else pd.Series("", index=training.index)
    _t = training[NC_TXT].fillna("").astype(str) if NC_TXT else pd.Series("", index=training.index)
    training["violation_type"] = [classify(a, b) for a, b in zip(_s, _t)]
    type_c = next((c for c in ("operation_type", "type") if c in training.columns), None)
    training["compliance_page"] = [
        compliance_url(oid, training[type_c].iloc[i] if type_c else "")
        for i, oid in enumerate(training[NC_OP])
    ] if NC_OP else ""

    # normalize output column names
    def has(name):
        for c in training.columns:
            if c.lower() == name:
                return c
        return None

    rename = {
        NC_OP: "operation_id",
        has("operation_name"): "operation_name",
        has("administrator_director_name") or has("director_name"): "contact_name",
        has("location_address") or has("address"): "location_address",
        has("mailing_address"): "mailing_address",
        has("city"): "city",
        has("county"): "county",
        has("zip"): "zip",
        has("phone") or has("phone_number"): "phone",
        has("email_address") or has("email"): "email",
        INSP_DATE: "activity_date",
        NC_STD: "standard_number_description",
        NC_RISK: "standard_risk_level",
        NC_TXT: "narrative",
    }
    rename = {k: v for k, v in rename.items() if k and k in training.columns}
    training = training.rename(columns=rename)

    out_cols = ["operation_id", "operation_name", "contact_name",
                "location_address", "mailing_address", "city",
                "county", "zip", "phone", "email", "activity_date",
                "standard_number_description", "standard_risk_level", "narrative",
                "violation_type", "compliance_page"]
    for c in out_cols:
        if c not in training.columns:
            training[c] = ""
    return training[out_cols].sort_values("activity_date", ascending=False).reset_index(drop=True)


if __name__ == "__main__":
    df = fetch_training_citations()
    print(f"\n{len(df)} training citations")
    if not df.empty:
        print(df.to_string(index=False))
