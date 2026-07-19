# GoHighLevel setup — weekly TX daycare training-citation funnel

This connects the weekly scraper to your GoHighLevel (GHL) account so that every
week the daycares cited for training violations are loaded, tagged, and dropped
into the right nurture sequence — and then booking / no-show moves happen
automatically.

**How the work is split**

- **The Python agent (`weekly_agent.py`)** does the once-a-week part: pull the
  citations, upsert each daycare as a contact, tag it, and enroll it in the
  correct *starting* nurture workflow.
- **Native GHL workflows** (built once, below) do the real-time part: when a
  contact books, or is marked no-show, GHL moves them between sequences. Python
  can't do this well because it only wakes up weekly; GHL triggers fire the
  instant the appointment changes.

---

## 1. Create an API token + find your Location ID

1. In GHL go to **Settings → Private Integrations → Create new integration**.
2. Give it these scopes: `contacts.write`, `contacts.readonly`,
   `workflows.readonly`. (Add `calendars.readonly` if you later want Python to
   read appointments.)
3. Copy the generated token — this is `GHL_TOKEN`.
4. Your **Location ID** (`GHL_LOCATION_ID`) is in **Settings → Business Profile**,
   or it's the `location/<ID>` segment in your GHL URL.

## 2. Create the tags

Create these contact tags (Settings → Tags), or let the agent create them on
first write — names must match the agent's config:

| Tag | Meaning |
|---|---|
| `tx-ccl-cited` | base tag: pulled from the compliance feed |
| `director-training` | cited for annual/ongoing training-hour gaps |
| `orientation-training` | cited for orientation gaps |

## 3. Build the two starting nurture workflows

Create two workflows and copy each one's ID from its URL
(`.../workflow/<WORKFLOW_ID>`):

### A. Director Training — Nurture  → `WF_DIRECTOR_NURTURE`
Goal: offer your director training that cures and prevents training-hour
deficiencies. Suggested steps:
- Trigger: *Contact Tag Added = `director-training`* (or leave triggerless and
  let the agent enroll — the agent calls the enroll endpoint directly).
- Email/SMS drip referencing the specific citation (the agent leaves the
  standard number + narrative + compliance-page link as a **note** on the
  contact, and stamps `state = TX`).
- A clear CTA to **book a call** (point it at your booking calendar).

### B. Orientation Training — Nurture  → `WF_ORIENTATION_NURTURE`
Goal: build a **custom on-demand orientation** for them, from their procedures
manual + employee handbook, aligned to the Texas orientation mandate.
Suggested steps:
- Intro message explaining the custom-orientation offer.
- A GHL **form/survey** that collects their **procedures manual** and **employee
  handbook** (file-upload fields) and basic program info.
- CTA to **book a call** to scope the build.
- Reference the state orientation topics you must align to — Texas
  [26 TAC §746.1301 / §746.1305](https://texreg.sos.state.tx.us/public/readtac$ext.ViewTAC?tac_view=5&ti=26&pt=1&ch=746)
  orientation requires covering, at minimum: operational policies &
  procedures, child abuse/neglect recognition & reporting, emergency
  procedures, safe sleep / SIDS (if under 24 mo.), medication administration,
  discipline & guidance, supervision/ratios, and health practices.

> The agent enrolls each contact in exactly **one** starting workflow (the
> primary program). If a facility was cited for both orientation and other
> training issues it still gets **both tags**, but only the primary
> (orientation by default — set via the `PRIORITY` env var) enrollment fires, so
> nobody gets double-messaged.

## 4. Build the booked-call branching (native trigger)

Create one workflow: **On Booked → Move to Booked-Call Nurture**
- Trigger: **Customer Booked Appointment** (scope to your calendar).
- Action **Remove from Workflows**: the two nurture workflows above.
- **If/Else by tag**:
  - has `orientation-training` → add tag `booked-orientation`, enroll in
    *Booked Call — Orientation* workflow.
  - else (`director-training`) → add tag `booked-director`, enroll in
    *Booked Call — Director* workflow.
- Each *Booked Call* workflow simply nurtures **until the appointment date**
  (reminders, prep material). Use a **Wait → until event/appointment time** step
  so it naturally ends at the call.

## 5. Build the no-show reschedule (native trigger)

Create one workflow: **On No-Show → Reschedule**
- Trigger: **Appointment Status** = `No Show`.
- Action **Remove from Workflows**: the Booked-Call workflows.
- Add tag `no-show`, remove `booked-*`.
- **If/Else by tag** (`orientation-training` vs `director-training`) → enroll in
  the matching **No-Show Reschedule** sequence (SMS/email pushing them back to
  the booking calendar). When they rebook, step 4's trigger fires again and the
  cycle continues.

This yields the full lifecycle you described:

```
cited → tagged (director|orientation) → nurture (offer)
         │ books
         ▼
     booked-call nurture (until appointment)
         │ no-show
         ▼
     no-show reschedule → (rebook loops back to booked-call)
```

## 6. Add the secrets to GitHub

In the repo: **Settings → Secrets and variables → Actions → New repository
secret**. Add:

| Secret | Value |
|---|---|
| `GHL_TOKEN` | Private Integration token from step 1 |
| `GHL_LOCATION_ID` | Location ID from step 1 |
| `WF_DIRECTOR_NURTURE` | workflow ID from step 3A |
| `WF_ORIENTATION_NURTURE` | workflow ID from step 3B |
| `TX_APP_TOKEN` | *(optional)* Socrata app token from data.texas.gov |

The scheduled action (`.github/workflows/tx-daycare-ghl-weekly.yml`) then runs
every Monday. Use **Actions → Run workflow** for a first manual test.

## 7. Test safely first

- Run `python weekly_agent.py` locally with **no** `GHL_TOKEN` set → **DRY-RUN**:
  it prints exactly which daycares, tags, and programs it would push, without
  touching GHL.
- Then set the token and run once manually and confirm the contacts, tags,
  notes, and workflow enrollment look right before relying on the schedule.

## Notes on contact data & outreach

- The open data reliably has **business name, address, city/county, and often a
  phone**; **email and a contact/director name are frequently blank**. The
  `compliance_page` link is included on every contact (as a note) so you or a
  GHL enrichment step can fill in the director name/email from the facility's
  public page.
- This is B2B outreach to businesses using public regulatory data. Keep your
  sequences compliant with SMS/email rules (clear identification, easy opt-out,
  and honor your calling/texting windows).
