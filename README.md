# Texas daycares cited last week for training-hour violations

Google Colab script that queries the Texas HHS **Search Texas Child Care**
compliance data and returns the daycares cited in the **last 7 days** for
**training-hour** violations.

## What it does

It joins **three** Texas HHS [Search Texas Child Care](https://childcare.hhs.texas.gov/)
open datasets on the [Texas Open Data Portal](https://data.texas.gov/), because
the citation, its date, and the facility's details live in separate tables:

| Dataset | ID | Provides | Join key |
|---|---|---|---|
| [Inspection/Investigation](https://data.texas.gov/See-Category-Tile/HHSC-CCL-Inspection-Investigation-Assessment-Data/m5q4-3y3d) | `m5q4-3y3d` | **when** (`activity_date`) | `activity_id` |
| [Non-Compliance](https://data.texas.gov/See-Category-Tile/HHSC-CCL-Non-Compliance-Data/tqgd-mf4x) | `tqgd-mf4x` | **what standard** was cited | `activity_id` → `operation_id` |
| [Operations](https://data.texas.gov/See-Category-Tile/HHSC-CCL-Daycare-and-Residential-Operations-Data/bc5r-88dy) | `bc5r-88dy` | **who / where** (name, city, county) | `operation_id` |

> The Non-Compliance table has **no citation date** of its own (only correction
> dates), which is why the inspection date has to come from `m5q4-3y3d`.

Steps:

1. Pull inspections in the last 7-day window → the recent `activity_id`s.
2. Pull the deficiencies for those activities and keep only **training-hour**
   citations — Chapter 746 (centers) / 747 (homes) professional-development
   standards in the `746.13xx` / `747.13xx` range (annual clock hours,
   pre-service training, orientation). See
   [26 TAC §746.1309](https://www.law.cornell.edu/regulations/texas/26-Tex-Admin-Code-SS-746-1309)
   — Texas requires **24 annual training clock hours** per caregiver.
3. Attach the activity date, then enrich with name / address / city / county /
   phone / email.
4. Add a `compliance_page` link to each facility's page on the public
   [Search Texas Child Care](https://childcare.hhs.texas.gov/) site
   (`.../Public/OperationDetails?operationId=<id>`), where the director/contact
   name, phone, and email are listed.
5. Tag each row with a `violation_type`, print a summary, and save/download a CSV.

## How to run

- Open `texas_daycare_training_citations_colab.ipynb` in
  [Google Colab](https://colab.research.google.com/) and choose
  **Runtime → Run all**, **or**
- Paste `texas_daycare_training_citations_colab.py` into a single Colab cell.

No API key required. A free Socrata app token (data.texas.gov → profile →
App Tokens) can be added to `APP_TOKEN` to avoid rate limiting.

## Config knobs (top of the script)

| Setting | Default | Meaning |
|---|---|---|
| `DAYS_BACK` | `7` | Size of the look-back window. |
| `ANCHOR` | `"auto"` | `"auto"` anchors to the newest activity date in the feed (the state data lags the calendar); `"today"` uses the real calendar date. |
| `STRICT_HOURS_ONLY` | `False` | `True` keeps only hour-count violations and drops generic orientation/CPR training rows. |

## Automated weekly agent → GoHighLevel

> **Setting it up? Follow [`SETUP.md`](SETUP.md)** — one ordered, click-by-click
> guide covering the whole system (GitHub, GoHighLevel, Clay, and the digest
> email). The files below are the reference detail behind it.


For the hands-off version, the same data logic is packaged as a weekly agent
that loads cited daycares straight into GoHighLevel:

| File | Purpose |
|---|---|
| `tx_ccl.py` | Reusable data module (`fetch_training_citations(...)`) — the notebook logic as a function. |
| `ghl.py` | Minimal GoHighLevel API v2 client (upsert contact, tag, note, enroll in workflow). |
| `weekly_agent.py` | Orchestrator: pull the week's citations → upsert each daycare → tag `director-training` / `orientation-training` → enroll in the matching nurture workflow. Runs in **DRY-RUN** if no GHL token is set. |
| `.github/workflows/tx-daycare-ghl-weekly.yml` | GitHub Actions cron that runs the agent every Monday. |
| `GOHIGHLEVEL_SETUP.md` | Step-by-step: API token, tags, the two nurture workflows, and the native booked-call / no-show branching. |
| `CLAY_ENRICHMENT.md` | Optional: enrich daycares missing an email via a Clay table (name+city → website → director → email → GHL). |
| `weekly_digest.py` | Reads GHL a couple days after the load and **emails you the daycares still missing an email** — the ones to enrich by hand. |
| `.github/workflows/tx-daycare-digest-weekly.yml` | GitHub Actions cron (Wednesdays) that sends the digest. |

### Weekly "enrich by hand" email

Because Clay enriches asynchronously and writes back into GHL, the accurate list
of who's *still* missing an email only exists in GHL after Clay runs. So a
second job runs **Wednesday** (≈2 days after the Monday load), finds the week's
`tx-ccl-cited` contacts with no email, and emails you the list + a CSV, each with
its phone and compliance-page link so you can fill in the director/email by hand.

Email goes over SMTP (a Gmail app password works). Add these repo secrets:
`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, and `DIGEST_TO` (recipient).
With no SMTP secrets set, `weekly_digest.py` runs in DRY-RUN and just prints the
digest.

The agent handles the weekly load + tag + enroll; the **booked-call** and
**no-show** transitions are handled by native GHL workflow triggers (real-time),
as described in `GOHIGHLEVEL_SETUP.md`. Quick start:

```bash
# from the repo root
python weekly_agent.py          # DRY-RUN: prints what it would push
# then set GHL_TOKEN, GHL_LOCATION_ID, WF_DIRECTOR_NURTURE, WF_ORIENTATION_NURTURE
```

## Note on data freshness

Texas refreshes these open datasets on the **20th of each month**, and the
newest activity dates in the feed can trail the calendar by days to weeks.
`ANCHOR="auto"` handles this by defining "last week" relative to the most
recent record actually present, and the script prints the exact date window
it used. If you need real-time citations for the current calendar week, the
underlying [Search Texas Child Care](https://childcare.hhs.texas.gov/) site is
the authoritative source.
