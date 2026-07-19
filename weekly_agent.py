"""Weekly agent: Texas training citations -> GoHighLevel.

Once a week this:
  1. Pulls daycares cited in the last week for TRAINING violations (tx_ccl).
  2. Upserts each into GoHighLevel as a contact (name / phone / email / address).
  3. Tags it `director-training` or `orientation-training` by violation type.
  4. Enrolls it in the matching nurture workflow (offer the cure/prevent training
     for directors; build a custom on-demand orientation for orientation gaps).

The booked-call and no-show branching is intentionally NOT done here -- it is
handled by native GoHighLevel workflow triggers ("Appointment Booked",
"Appointment Status = No Show"), which react in real time. See
GOHIGHLEVEL_SETUP.md. This agent only gets the right people into the right
starting sequence with the right tag; GHL takes over from there.

Idempotency: GHL upsert dedupes by email/phone. A contact that already carries
the program tag is skipped (not re-enrolled, no duplicate note), so running the
agent again -- or a facility being cited two weeks in a row -- won't spam them.

Configuration comes from environment variables (see CONFIG below). With no GHL
token set, the agent runs in DRY-RUN mode and just prints what it would do.
"""

import os
import re
import sys
import datetime as dt

import tx_ccl

# ---------------------------------------------------------------------------
# CONFIG (environment variables; sensible defaults for tags)
# ---------------------------------------------------------------------------
GHL_TOKEN        = os.environ.get("GHL_TOKEN", "").strip()
GHL_LOCATION_ID  = os.environ.get("GHL_LOCATION_ID", "").strip()
TX_APP_TOKEN     = os.environ.get("TX_APP_TOKEN", "").strip()   # optional Socrata token

# Workflow IDs (from the GHL workflow URL). Required to actually enroll.
WF_DIRECTOR_NURTURE    = os.environ.get("WF_DIRECTOR_NURTURE", "").strip()
WF_ORIENTATION_NURTURE = os.environ.get("WF_ORIENTATION_NURTURE", "").strip()

# Tags
TAG_BASE        = os.environ.get("TAG_BASE", "tx-ccl-cited")
TAG_DIRECTOR    = os.environ.get("TAG_DIRECTOR", "director-training")
TAG_ORIENTATION = os.environ.get("TAG_ORIENTATION", "orientation-training")

DAYS_BACK = int(os.environ.get("DAYS_BACK") or "7")
ANCHOR    = os.environ.get("ANCHOR") or "auto"

# Clay enrichment (optional). If set, daycares that have NO email in the open
# data are POSTed to a Clay table webhook. Clay resolves name+city -> website ->
# director/owner -> email, then writes the finished contact into GoHighLevel
# (with the program tag + workflow) via Clay's native GHL action. See
# CLAY_ENRICHMENT.md. Facilities that already have an email skip Clay and are
# loaded straight to GHL by this agent.
CLAY_WEBHOOK_URL = os.environ.get("CLAY_WEBHOOK_URL", "").strip()

# When a facility has BOTH orientation and other training deficiencies, which
# program wins the (single) enrollment. Tags for both are still applied.
# Order = priority.
PRIORITY = os.environ.get("PRIORITY", "orientation,director").split(",")

# Only load daycares cited for something our two offers actually address:
# orientation, or a training-hours gap the director-training offer fixes. A
# facility whose citations are ONLY outside this set (e.g. pediatric CPR /
# first aid) is skipped. Override with ALLOWED_VIOLATION_TYPES if needed.
ALLOWED_TYPES = set(t.strip() for t in os.environ.get(
    "ALLOWED_VIOLATION_TYPES",
    "Orientation,Director training"
).split(",") if t.strip())

# Manual exclude list: operation IDs to never load (e.g. franchise-tax
# delinquent, or otherwise disqualified after review). Comma-separated.
EXCLUDE_IDS = set(x.strip() for x in
                  os.environ.get("EXCLUDE_OPERATION_IDS", "").split(",") if x.strip())

# Drop residential operations (RTC / GRO / child-placing) -- this is a daycare
# funnel. Set DAYCARE_ONLY=false to include them.
DAYCARE_ONLY = os.environ.get("DAYCARE_ONLY", "true").strip().lower() in ("1", "true", "yes")
RESIDENTIAL_RE = re.compile(
    r"residential|general residential|\bgro\b|treatment center|child.?placing", re.I)

# Only pursue daycares whose citation is STILL OPEN. If the deficiency was
# already corrected (fixed at inspection, or has a corrected/verified date),
# skip it -- no point offering training for a problem they've fixed. Set
# ONLY_UNCORRECTED=false to include corrected ones too.
ONLY_UNCORRECTED = os.environ.get("ONLY_UNCORRECTED", "true").strip().lower() in ("1", "true", "yes")

# DRY-RUN if there's no GHL token, OR if explicitly forced (the "dry run" toggle
# on the GitHub Actions "Run workflow" button sets DRY_RUN_FORCE=true).
_FORCE_DRY = os.environ.get("DRY_RUN_FORCE", "").strip().lower() in ("1", "true", "yes")
DRY_RUN = _FORCE_DRY or not (GHL_TOKEN and GHL_LOCATION_ID)


# ---------------------------------------------------------------------------
# Mapping: violation_type -> program
# ---------------------------------------------------------------------------
def program_for(violation_type):
    """Which offer this citation maps to."""
    return "orientation" if violation_type == "Orientation" else "director"


PROGRAM_TAG = {"director": TAG_DIRECTOR, "orientation": TAG_ORIENTATION}
PROGRAM_WF  = {"director": WF_DIRECTOR_NURTURE, "orientation": WF_ORIENTATION_NURTURE}


def pick_primary(programs):
    for p in PRIORITY:
        if p in programs:
            return p
    return sorted(programs)[0]


def clean_phone(v):
    s = "".join(ch for ch in str(v) if ch.isdigit())
    return s or None


def clay_payload(op_id, rows, primary, tags):
    """Flat record for the Clay table webhook. Clay enriches (website -> owner ->
    email) and writes to GHL, so we hand it everything needed to create/route
    the contact even if enrichment finds nothing extra."""
    r0 = rows[0]
    return {
        "operation_id": op_id,
        "business_name": r0["operation_name"] or f"Operation {op_id}",
        "contact_name": r0.get("contact_name", ""),
        "address": r0["location_address"],
        "city": r0["city"],
        "state": "TX",
        "postal_code": str(r0["zip"]),
        "county": r0["county"],
        "phone": clean_phone(r0["phone"]) or "",
        "email": r0["email"] or "",           # usually blank -> Clay fills it
        "program": primary,                    # "director" | "orientation"
        "tags": ",".join(tags),
        "violation_types": ", ".join(sorted({r["violation_type"] for r in rows})),
        "cited_date": r0["activity_date"][:10],
        "compliance_page": r0["compliance_page"],
    }


def post_to_clay(payload):
    import requests
    r = requests.post(CLAY_WEBHOOK_URL, json=payload, timeout=30)
    if not r.ok:
        raise RuntimeError(f"Clay webhook {r.status_code}: {r.text[:200]}")


def build_note(rows):
    """Human-readable citation summary for the contact's timeline."""
    lines = ["Texas CCR training citation(s) pulled from the Search Texas Child "
             "Care compliance feed:\n"]
    for r in rows:
        lines.append(
            f"- {r['activity_date'][:10]}  [{r['violation_type']}]  "
            f"{r['standard_number_description']} "
            f"(risk: {r['standard_risk_level']})\n    {r['narrative']}")
    lines.append(f"\nFacility compliance page: {rows[0]['compliance_page']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print(f"=== TX daycare -> GHL weekly agent  ({dt.date.today()}) ===")
    print(f"Mode: {'DRY-RUN (no GHL token)' if DRY_RUN else 'LIVE'}")

    df = tx_ccl.fetch_training_citations(
        days_back=DAYS_BACK, anchor=ANCHOR, app_token=TX_APP_TOKEN)
    if df.empty:
        print("No training citations this week. Nothing to load.")
        return

    # group all citations by facility (one contact per daycare)
    facilities = {}
    for _, row in df.iterrows():
        facilities.setdefault(str(row["operation_id"]), []).append(row.to_dict())
    print(f"{len(df)} citations across {len(facilities)} daycares.\n")

    ghl = None
    if not DRY_RUN:
        from ghl import GHL
        ghl = GHL(GHL_TOKEN, GHL_LOCATION_ID)

    loaded = skipped = enriched = errors = no_contact = out_of_scope = corrected_skip = 0
    for op_id, rows in facilities.items():
        r0 = rows[0]
        name = r0["operation_name"] or f"Operation {op_id}"
        types = {r["violation_type"] for r in rows}
        vt = ", ".join(sorted(types))

        # Manual exclude list (e.g. franchise-tax delinquent).
        if str(op_id) in EXCLUDE_IDS:
            print(f"- {name} ({r0['city']}, {r0['county']}) -> SKIP: on manual "
                  f"exclude list (operation_id {op_id})")
            out_of_scope += 1
            continue

        # Scope filters: daycares only, and only orientation/director-training
        # (training-hours) citations.
        if DAYCARE_ONLY and RESIDENTIAL_RE.search(r0.get("operation_type", "")):
            print(f"- {name} ({r0['city']}, {r0['county']}) -> SKIP: residential "
                  f"({r0.get('operation_type','')}), not a daycare")
            out_of_scope += 1
            continue
        allowed_rows = [r for r in rows if r["violation_type"] in ALLOWED_TYPES]
        if not allowed_rows:
            print(f"- {name} ({r0['city']}, {r0['county']}) -> SKIP: not "
                  f"orientation/director-training (types: {vt})")
            out_of_scope += 1
            continue

        # Keep only citations still open (unless ONLY_UNCORRECTED is off).
        open_rows = [r for r in allowed_rows
                     if not ONLY_UNCORRECTED
                     or str(r.get("is_corrected", "")).strip().lower() != "yes"]
        if not open_rows:
            print(f"- {name} ({r0['city']}, {r0['county']}) -> SKIP: citation "
                  f"already corrected")
            corrected_skip += 1
            continue

        programs = {program_for(r["violation_type"]) for r in open_rows}
        primary = pick_primary(programs)
        tags = [TAG_BASE] + [PROGRAM_TAG[p] for p in programs]

        wf = PROGRAM_WF[primary]
        phone = clean_phone(r0["phone"])
        email = r0["email"] or None
        # Route: no email + Clay configured -> enrich via Clay (which writes to
        # GHL). Otherwise this agent loads GHL directly.
        via_clay = bool(CLAY_WEBHOOK_URL) and not email
        dest = "Clay->GHL (enrich)" if via_clay else "GHL direct"
        print(f"- {name} ({r0['city']}, {r0['county']}) "
              f"-> {dest} | primary: {primary} | tags: {tags} | types: {vt}")
        print(f"    contact: {r0.get('contact_name') or 'n/a'}  |  "
              f"phone: {r0.get('phone') or 'n/a'}  |  email: {r0.get('email') or 'n/a'}")
        print(f"    citation status: OPEN (uncorrected)  |  page: {r0.get('compliance_page') or 'n/a'}")

        if DRY_RUN:
            if via_clay:
                enriched += 1
            else:
                loaded += 1
            continue

        # Isolate each facility so one bad record can't kill the whole run.
        try:
            if via_clay:
                post_to_clay(clay_payload(op_id, rows, primary, tags))
                print("    sent to Clay for enrichment (Clay will write to GHL)")
                enriched += 1
                continue

            # GoHighLevel needs at least a phone or an email to create a contact.
            if not phone and not email:
                print("    skipped: no phone or email in the open data -- can't "
                      "create a GHL contact. Enrich by hand from "
                      f"{r0['compliance_page']}  (or set CLAY_WEBHOOK_URL to enrich)")
                no_contact += 1
                continue

            # director/administrator name from the open data, if present
            contact_name = (r0.get("contact_name") or "").strip()
            first = last = None
            if contact_name:
                parts = contact_name.split()
                first, last = parts[0], (" ".join(parts[1:]) or None)

            contact_id, existing = ghl.upsert_contact(
                name=(contact_name or name), first_name=first, last_name=last,
                company_name=name, phone=phone, email=email,
                address=r0["location_address"], city=r0["city"], state="TX",
                postal_code=r0["zip"], source="TX CCR weekly", tags=tags,
                website=r0["compliance_page"])  # stored so the digest can link it

            # already in this program? skip enrollment + note (no double-touch)
            program_tag = PROGRAM_TAG[primary]
            if program_tag in (existing or []):
                print(f"    already tagged '{program_tag}' -> skip enroll")
                skipped += 1
                continue

            ghl.add_note(contact_id, build_note(rows))
            if wf:
                ghl.add_to_workflow(contact_id, wf)
                print(f"    enrolled in workflow {wf}")
            else:
                print(f"    (!) no workflow id set for '{primary}' -- tagged only")
            loaded += 1
        except Exception as e:
            print(f"    ! error for {name}: {e}")
            errors += 1

    print(f"\nDone. {loaded} loaded to GHL, {enriched} sent to Clay, "
          f"{skipped} already enrolled, {no_contact} skipped (no phone/email), "
          f"{corrected_skip} skipped (already corrected), "
          f"{out_of_scope} out of scope (not daycare / not orientation-director), "
          f"{errors} errored.")
    if DRY_RUN:
        print("Set GHL_TOKEN and GHL_LOCATION_ID to run live"
              + (" (and CLAY_WEBHOOK_URL to enrich)." if not CLAY_WEBHOOK_URL else "."))
    # Fail the run only if nothing succeeded but something errored -- that signals
    # a systemic problem (bad token, wrong IDs) rather than one odd record.
    if errors and not (loaded or enriched):
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
