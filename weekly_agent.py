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

DAYS_BACK = int(os.environ.get("DAYS_BACK", "7"))
ANCHOR    = os.environ.get("ANCHOR", "auto")

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

    loaded = skipped = enriched = 0
    for op_id, rows in facilities.items():
        r0 = rows[0]
        programs = {program_for(r["violation_type"]) for r in rows}
        primary = pick_primary(programs)
        tags = [TAG_BASE] + [PROGRAM_TAG[p] for p in programs]
        name = r0["operation_name"] or f"Operation {op_id}"

        wf = PROGRAM_WF[primary]
        vt = ", ".join(sorted({r["violation_type"] for r in rows}))
        # Route: no email + Clay configured -> enrich via Clay (which writes to
        # GHL). Otherwise this agent loads GHL directly.
        via_clay = CLAY_WEBHOOK_URL and not r0["email"]
        dest = "Clay->GHL (enrich)" if via_clay else "GHL direct"
        print(f"- {name} ({r0['city']}, {r0['county']}) "
              f"-> {dest} | primary: {primary} | tags: {tags} | types: {vt}")

        if DRY_RUN:
            if via_clay:
                enriched += 1
            else:
                loaded += 1
            continue

        if via_clay:
            post_to_clay(clay_payload(op_id, rows, primary, tags))
            print("    sent to Clay for enrichment (Clay will write to GHL)")
            enriched += 1
            continue

        contact_id, existing = ghl.upsert_contact(
            name=name, company_name=name,
            phone=clean_phone(r0["phone"]), email=(r0["email"] or None),
            address=r0["location_address"], city=r0["city"], state="TX",
            postal_code=r0["zip"], source="TX CCR weekly", tags=tags,
            website=r0["compliance_page"])   # stored so the weekly digest can link it

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

    print(f"\nDone. {loaded} loaded to GHL directly, {enriched} sent to Clay "
          f"for enrichment, {skipped} skipped (already enrolled).")
    if DRY_RUN:
        print("Set GHL_TOKEN and GHL_LOCATION_ID to run live"
              + (" (and CLAY_WEBHOOK_URL to enrich)." if not CLAY_WEBHOOK_URL else "."))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
