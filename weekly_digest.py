"""Weekly 'enrich by hand' digest.

Runs a couple of days AFTER the loader (so Clay has had time to enrich and write
back to GoHighLevel), reads the contacts this week's run created, and emails you
the ones that STILL have no email address -- i.e. the daycares you need to
enrich by hand from their compliance page.

Why after, and from GHL: Clay enrichment is asynchronous and writes results back
into GHL, so the only accurate "what's still missing" list lives in GHL once
Clay has finished. This job reads it there.

Selection: contacts carrying the base tag (tx-ccl-cited) added within the last
DIGEST_LOOKBACK_DAYS and having no email.

Email transport: any SMTP server (Gmail app password works). With no SMTP creds
set, it runs in DRY-RUN and just prints the digest + writes the CSV.
"""

import os
import sys
import csv
import ssl
import smtplib
import datetime as dt
from email.message import EmailMessage

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
GHL_TOKEN       = os.environ.get("GHL_TOKEN", "").strip()
GHL_LOCATION_ID = os.environ.get("GHL_LOCATION_ID", "").strip()
TAG_BASE        = os.environ.get("TAG_BASE", "tx-ccl-cited")
TAG_DIRECTOR    = os.environ.get("TAG_DIRECTOR", "director-training")
TAG_ORIENTATION = os.environ.get("TAG_ORIENTATION", "orientation-training")
LOOKBACK_DAYS   = int(os.environ.get("DIGEST_LOOKBACK_DAYS", "4"))

DIGEST_TO   = os.environ.get("DIGEST_TO", "infojennifermurchison@gmail.com").strip()
SMTP_HOST   = os.environ.get("SMTP_HOST", "").strip()
SMTP_PORT   = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER   = os.environ.get("SMTP_USER", "").strip()
SMTP_PASS   = os.environ.get("SMTP_PASS", "").strip()
SMTP_FROM   = os.environ.get("SMTP_FROM", SMTP_USER).strip()

CAN_EMAIL = bool(SMTP_HOST and SMTP_USER and SMTP_PASS and DIGEST_TO)


# ---------------------------------------------------------------------------
def _parse_dt(value):
    if not value:
        return None
    s = str(value).replace("Z", "+00:00")
    try:
        return dt.datetime.fromisoformat(s)
    except Exception:
        try:                                    # epoch millis fallback
            return dt.datetime.fromtimestamp(int(value) / 1000, tz=dt.timezone.utc)
        except Exception:
            return None


def _has_tag(contact, tag):
    return any(str(t).lower() == tag.lower() for t in (contact.get("tags") or []))


def _program(contact):
    if _has_tag(contact, TAG_ORIENTATION):
        return "orientation"
    if _has_tag(contact, TAG_DIRECTOR):
        return "director"
    return ""


def _custom_field(contact, key_substr):
    """Best-effort read of a custom field value (compliance page) if present."""
    for cf in (contact.get("customFields") or contact.get("customField") or []):
        k = str(cf.get("id", "")) + str(cf.get("key", ""))
        if key_substr.lower() in k.lower():
            return cf.get("value") or cf.get("field_value") or ""
    return ""


def gather():
    from ghl import GHL
    ghl = GHL(GHL_TOKEN, GHL_LOCATION_ID)
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=LOOKBACK_DAYS)
    rows = []
    for c in ghl.list_contacts():
        if not _has_tag(c, TAG_BASE):
            continue
        if (c.get("email") or "").strip():
            continue                            # already contactable
        added = _parse_dt(c.get("dateAdded"))
        if added and added.tzinfo is None:
            added = added.replace(tzinfo=dt.timezone.utc)
        if added and added < cutoff:
            continue                            # older cohort
        name = c.get("companyName") or c.get("name") or \
            f"{c.get('firstName','')} {c.get('lastName','')}".strip()
        rows.append({
            "business_name": name,
            "phone": c.get("phone", ""),
            "city": c.get("city", ""),
            "state": c.get("state", ""),
            "program": _program(c),
            # the loader stores the facility's compliance page in `website`;
            # fall back to a custom field if a Clay mapping used one instead
            "compliance_page": c.get("website") or _custom_field(c, "compliance"),
            "contact_id": c.get("id", ""),
        })
    return rows


# ---------------------------------------------------------------------------
def write_csv(rows, path):
    cols = ["business_name", "phone", "city", "state", "program",
            "compliance_page", "contact_id"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def render_body(rows, week):
    if not rows:
        return f"No daycares from the {week} cohort are missing an email. " \
               "Nothing to enrich by hand this week. \U0001F389"
    lines = [f"{len(rows)} daycare(s) from the {week} cohort still have NO email "
             "after enrichment — enrich these by hand from their compliance page:\n"]
    for r in rows:
        loc = ", ".join(x for x in (r["city"], r["state"]) if x)
        prog = f" [{r['program']}]" if r["program"] else ""
        lines.append(f"• {r['business_name']}{prog} — {loc}"
                     f"\n    phone: {r['phone'] or 'n/a'}"
                     f"\n    page:  {r['compliance_page'] or 'n/a'}")
    lines.append("\n(Full list attached as CSV.)")
    return "\n".join(lines)


def send_email(subject, body, attachment_path):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM or SMTP_USER
    msg["To"] = DIGEST_TO
    msg.set_content(body)
    with open(attachment_path, "rb") as f:
        msg.add_attachment(f.read(), maintype="text", subtype="csv",
                           filename=os.path.basename(attachment_path))
    ctx = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls(context=ctx)
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)


# ---------------------------------------------------------------------------
def main():
    week = dt.date.today().isoformat()
    print(f"=== TX daycare 'enrich by hand' digest ({week}) ===")
    if not (GHL_TOKEN and GHL_LOCATION_ID):
        print("No GHL credentials -> cannot read the cohort. Set GHL_TOKEN and "
              "GHL_LOCATION_ID.")
        return

    rows = gather()
    print(f"{len(rows)} contact(s) tagged '{TAG_BASE}' in the last "
          f"{LOOKBACK_DAYS} days with no email.")
    out = f"enrich_by_hand_{week}.csv"
    write_csv(rows, out)
    body = render_body(rows, week)
    subject = f"[TX daycares] {len(rows)} to enrich by hand — week of {week}"

    if CAN_EMAIL:
        send_email(subject, body, out)
        print(f"Emailed digest to {DIGEST_TO}.")
    else:
        print("\n--- DRY-RUN (no SMTP creds); digest below ---\n")
        print(subject + "\n")
        print(body)
        print(f"\nCSV written to {out}. Set SMTP_HOST/PORT/USER/PASS to email it.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
