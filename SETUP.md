# Full setup — Texas daycare → GoHighLevel weekly automation

Follow these in order. Each part says exactly what to click, what to copy, and
where to paste it. Budget ~60–90 minutes the first time (most of it is building
the GoHighLevel workflows).

**What you're building**

```
MONDAY   weekly_agent.py   scrape cited daycares → tag director/orientation
                           → load to GHL (or send no-email ones to Clay)
 (async) Clay              name+city → website → director → email → write to GHL
WEDNESDAY weekly_digest.py → email you the daycares still missing an email
GHL (real time)            book → booked-call sequence;  no-show → reschedule
```

You'll need accounts for: **GitHub** (has the code), **GoHighLevel**, optionally
**Clay** (enrichment), and a **Gmail** address for the digest email.

---

## Part 1 — Confirm the code is here

The automation lives directly on the `main` branch of **this** repo
(`infojennifermurchison-del/DaycareCitations`) — no pull request to merge.
Scheduled GitHub Actions only run from the default branch, and that's where
these files are.

1. Open this repo's **Actions** tab.
2. Confirm two workflows appear in the left sidebar:
   **"TX daycare training citations → GoHighLevel (weekly)"** and
   **"TX daycare 'enrich by hand' digest (weekly)"**.
3. They do nothing yet because the secrets aren't set — that's Part 5, and
   nothing sends until you add them.

> **Secrets are per-repository.** They must be added under **this**
> `DaycareCitations` repo (Part 5). Any secrets you added to a different repo
> won't be visible here — re-add them here.

---

## Part 2 — GoHighLevel

### 2.1 Create a Private Integration token

1. In GoHighLevel, make sure you're in the **sub-account (Location)** you want
   these contacts in (not the Agency view).
2. **Settings → Private Integrations → + Create new integration.**
3. Name it `Texas daycare loader`.
4. Enable these scopes (check the boxes):
   - **View Contacts**
   - **Edit Contacts**
   - **View Workflows**
5. Click **Create**, then **copy the token** (it starts with `pit-...`).
   You won't see it again — paste it somewhere safe for now.
   → This is your **`GHL_TOKEN`**.

### 2.2 Find your Location ID

1. Look at your browser URL while inside the sub-account. It looks like:
   `https://app.gohighlevel.com/v2/location/ABC123xyz.../dashboard`
2. The value between `/location/` and the next `/` is your Location ID.
   → This is your **`GHL_LOCATION_ID`**.

### 2.3 Create the tags

**Settings → Tags → Add Tag**, create these three (spelled exactly):

- `tx-ccl-cited`
- `director-training`
- `orientation-training`

(These are the defaults the code uses. If you want different names, you'll set
`TAG_BASE` / `TAG_DIRECTOR` / `TAG_ORIENTATION` as secrets in Part 5.)

### 2.4 Build the two nurture workflows

Go to **Automation → Workflows → + Create Workflow → Start from scratch.**

**Workflow A — "Director Training – Nurture"**
- Trigger: leave it with no trigger, OR add trigger **Contact Tag** →
  `director-training` (either works; the agent also enrolls directly).
- Add your drip steps (emails/SMS) offering your director training. Somewhere
  early, add a **clear "Book a call" link** pointing at your GHL calendar.
- **Publish** the workflow (toggle top-right to **Publish**).
- Open the workflow and copy its ID from the URL:
  `.../workflows/<THIS_IS_THE_ID>/...`
  → This is your **`WF_DIRECTOR_NURTURE`**.

**Workflow B — "Orientation Training – Nurture"**
- Same idea, but this sequence offers to **build their custom on-demand
  orientation**. Add a **Form/Survey** step that collects their **procedures
  manual** and **employee handbook** (file-upload fields), plus a "Book a call"
  link.
- Publish and copy its ID → **`WF_ORIENTATION_NURTURE`**.

> Align the orientation content to the Texas mandate — 26 TAC §746.1301 /
> §746.1305 orientation must cover: operational policies & procedures, child
> abuse/neglect recognition & reporting, emergency procedures, safe sleep/SIDS
> (under 24 mo.), medication administration, discipline & guidance,
> supervision/ratios, and health practices.

### 2.5 Build the booked-call branching (real-time)

**Create Workflow → "On Booked → Booked-Call Nurture"**
1. Trigger: **Customer Booked Appointment** → filter to your calendar.
2. Action **Remove from Workflow** → select *both* nurture workflows (A and B).
3. Add an **If/Else** condition on **Contact Tag**:
   - **If `orientation-training`** → add tag `booked-orientation` → enroll in a
     "Booked Call – Orientation" workflow (reminders/prep until the call).
   - **Else** → add tag `booked-director` → enroll in "Booked Call – Director".
4. In each Booked-Call workflow, use **Wait → until appointment time** so it
   naturally ends at the call.
5. Publish.

### 2.6 Build the no-show reschedule (real-time)

**Create Workflow → "On No-Show → Reschedule"**
1. Trigger: **Appointment Status** → status **No Show** (your calendar).
2. Action **Remove from Workflow** → the two Booked-Call workflows.
3. Add tag `no-show`; remove `booked-director` / `booked-orientation`.
4. **If/Else** on tag → enroll in the matching **No-Show Reschedule** sequence
   (SMS/email pushing them back to your booking link). When they rebook, 2.5's
   trigger fires again automatically.
5. Publish.

You now have the full lifecycle. Keep `GHL_TOKEN`, `GHL_LOCATION_ID`,
`WF_DIRECTOR_NURTURE`, `WF_ORIENTATION_NURTURE` for Part 5.

---

## Part 3 — Clay enrichment (optional but recommended)

Skip this if you don't want auto-enrichment; the loader will just push name +
phone + compliance link and every no-email daycare shows up on your Wednesday
digest instead.

1. In Clay: **Create a table → Import → Webhook / HTTP API.** Copy the **webhook
   URL** it shows. → This is your **`CLAY_WEBHOOK_URL`**.
2. Add enrichment columns, in order:
   - **Find Company** from `business_name` + `city` + `state` → gets a domain.
   - **Find People / Enrich Contacts** at that company, filtered to titles:
     `Owner, Director, Administrator, Executive Director, Operator`.
   - **Find Work Email** (waterfall) on that person.
3. Add the **GoHighLevel** action ("Create/Update Contact"):
   - Map first/last name (found), `email` (found), `phone`, `business_name`.
   - Map **Tags** ← the incoming `tags` column.
   - Map `compliance_page` → the GHL **Website** field (so the digest can link
     it if enrichment misses).
4. Set the table to **auto-run on new rows.**

Full detail and the exact fields the agent sends are in `CLAY_ENRICHMENT.md`.

---

## Part 4 — Gmail app password (for the Wednesday digest email)

The digest emails you over SMTP. Gmail needs an "app password" (not your normal
password):

1. Turn on **2-Step Verification**: `myaccount.google.com` → **Security** →
   **2-Step Verification** (if it isn't already on).
2. Go to **App passwords** (`myaccount.google.com/apppasswords`), name it
   `TX daycare digest`, and **generate**. Copy the 16-character password.
3. These become your secrets:
   - `SMTP_HOST` = `smtp.gmail.com`
   - `SMTP_PORT` = `587`
   - `SMTP_USER` = your full Gmail address
   - `SMTP_PASS` = the 16-character app password (no spaces)
   - `DIGEST_TO` = where you want the digest sent (your email)

---

## Part 5 — Add the secrets to GitHub

Repo → **Settings → Secrets and variables → Actions → New repository secret.**
Add each of these (name on the left, value from the steps above):

| Secret | From | Required? |
|---|---|---|
| `GHL_TOKEN` | Part 2.1 | ✅ |
| `GHL_LOCATION_ID` | Part 2.2 | ✅ |
| `WF_DIRECTOR_NURTURE` | Part 2.4 A | ✅ |
| `WF_ORIENTATION_NURTURE` | Part 2.4 B | ✅ |
| `CLAY_WEBHOOK_URL` | Part 3 | only if using Clay |
| `SMTP_HOST` | Part 4 | for digest email |
| `SMTP_PORT` | Part 4 | for digest email |
| `SMTP_USER` | Part 4 | for digest email |
| `SMTP_PASS` | Part 4 | for digest email |
| `DIGEST_TO` | Part 4 | for digest email |
| `TX_APP_TOKEN` | data.texas.gov (optional) | no |

---

## Part 6 — Test in order (before trusting the schedule)

You can do all of this from the GitHub website — no coding.

### 6.1 Safe preview (dry run — writes nothing)
1. Repo → **Actions** tab.
2. In the left sidebar click **"TX daycare training citations → GoHighLevel
   (weekly)"**.
3. Click **Run workflow** (right side). Leave **Dry run** ticked (the default).
4. Click the green **Run workflow** button. Refresh; click the new run, then the
   **load-citations** job to watch the log.
5. Confirm it prints, per daycare, `GHL direct` or `Clay->GHL (enrich)` plus the
   tags — and ends `Mode: DRY-RUN`. Nothing was written to GoHighLevel.

### 6.2 Live loader run (writes to GHL)
1. Same screen: **Run workflow**, but this time **untick Dry run**, then Run.
2. Watch the log; it should end with a count of contacts loaded/enrolled and
   `Mode: LIVE`.
3. In GoHighLevel → **Contacts**, filter by tag `tx-ccl-cited`. Confirm the new
   records, their `director-training` / `orientation-training` tags, the note
   with the citation detail, and that they entered the right nurture workflow
   (open a contact → **Automations** shows active workflows).

### 6.3 Digest test
1. If you set up Clay, give it ~an hour to enrich first.
2. Actions → **"TX daycare 'enrich by hand' digest (weekly)" → Run workflow →
   Run.**
3. Check your inbox (`DIGEST_TO`) for the email + CSV of daycares still missing
   an email. An empty/"nothing to enrich" message means everyone has an email —
   good news. If it doesn't arrive, open the run log (it prints the digest even
   when the email send fails, so you'll see the cause).

---

## Part 7 — Go live

Nothing else to do. The schedules are already active from the merge in Part 1:
- **Loader:** Mondays 13:00 UTC (~8am Central).
- **Digest:** Wednesdays 14:00 UTC (~9am Central).

To change times, edit the `cron:` line in each file under
`.github/workflows/` (times are UTC).

---

## Part 8 — Troubleshooting

| Symptom | Fix |
|---|---|
| Loader run "0 citations" | Normal on a light week, or the feed lags. Increase `DAYS_BACK` secret (e.g. `14`). |
| `GHL ... 401/403` | Token wrong/expired or missing a scope. Recreate in 2.1. |
| Contacts load but no workflow enrollment | `WF_*` IDs missing/wrong — recheck the workflow URL in 2.4. |
| Digest email never arrives | SMTP secret wrong, or Gmail app password has spaces. Re-run 6.3 and read the Actions log. |
| Digest lists everyone (Clay found nothing) | Expected for small/in-home daycares with no website. Enrich those by hand from the linked page. |
| Scheduled runs never fire | The workflow files must be on `main` (Part 1). Scheduled runs also pause after ~60 days of repo inactivity — push any commit to resume. |

### Things I could not verify remotely
The GoHighLevel API calls (contact upsert/tag/note/enroll, and the contact
**read** used by the digest) and the SMTP send were written to the documented
specs but not executed against your live account from here. The first live runs
in Part 6 are the real test — if any GHL field name or pagination detail differs
in your account, send me the Actions log and it's a quick fix.
