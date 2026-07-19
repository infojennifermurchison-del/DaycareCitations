# Clay enrichment for daycares missing an email

The Texas open data usually gives you a business name, address, and phone — but
**no email and no director/owner name**. This wires [Clay](https://clay.com) in
to fill those gaps for the facilities that need it, then hand the finished
contact to GoHighLevel.

## How it fits

The weekly agent routes each cited daycare:

- **Has an email already** → agent loads it straight into GHL (tags + workflow).
- **No email** → agent POSTs it to a **Clay table webhook**. Clay resolves the
  business to a website, finds the director/owner, finds a work email, and
  writes the completed contact into GHL — with the same program tag the agent
  sent, so routing is preserved.

```
weekly_agent.py ──POST──▶ Clay table (webhook source)
                             ├─ Find company by name + city  → domain
                             ├─ Find people (owner/director)  → name, title, LinkedIn
                             ├─ Find work email               → email
                             └─ Write to GoHighLevel          → contact + tag + workflow
```

Enable it by setting one secret: `CLAY_WEBHOOK_URL`. Leave it unset and the
agent just loads everyone into GHL directly (phone + compliance link only).

## Honest expectations

Clay is keyed on a company **website/LinkedIn**. Large centers and chains
(YMCA, franchises) enrich well. Small independent and in-home daycares often
have **no website at all**, so Clay will return nothing extra for a meaningful
share of them. Those still reach GHL with name + phone + address + the
compliance-page link, so they're not lost — you just work them by phone or fill
the contact from the facility page. Treat enrichment as "lift where possible,"
not "email for every row."

## Build the Clay table (once)

1. **New table → Import → Webhook.** Copy the webhook URL Clay gives you — this
   is `CLAY_WEBHOOK_URL`. The agent sends these fields per row:

   | Field | Example |
   |---|---|
   | `business_name` | `Premier Early Learning Center` |
   | `address`, `city`, `state`, `postal_code`, `county` | `3450 Roosevelt`, `San Antonio`, `TX`, `78214`, `Bexar` |
   | `phone` | `2105551212` (digits only; may be blank) |
   | `email` | usually blank — Clay fills it |
   | `program` | `director` or `orientation` |
   | `tags` | `tx-ccl-cited,orientation-training` |
   | `violation_types` | `Orientation, Pediatric CPR / First Aid` |
   | `cited_date` | `2026-07-02` |
   | `compliance_page` | link to the facility's public page |

2. **Find the company / website.** Add a Clay enrichment column that finds a
   company domain from `business_name` + `city` + `state` (Clay's
   company-search / "Find Company" enrichments). Expect misses on tiny operators.

3. **Find the decision-maker.** Add a "Find People" / enrich-contacts column
   filtered to owner/director titles
   (`Owner, Director, Administrator, Executive Director, Operator`).

4. **Find the work email.** Add a "Find Work Email" (waterfall) column on the
   person from step 3. Optionally add email validation.

5. **Write to GoHighLevel.** Add the **GoHighLevel** integration action
   ("Create/Update Contact"):
   - Map first/last name (from step 3), `email` (step 4), `phone`,
     `business_name` → company, address fields.
   - Map **Tags** ← the `tags` column (so `director-training` /
     `orientation-training` carry through).
   - Enroll in workflow: either add the workflow step here keyed off `program`,
     or let your existing GHL "tag added" trigger start the nurture (see
     `GOHIGHLEVEL_SETUP.md`). Using the tag trigger keeps one source of truth.
   - Map `compliance_page` → the GHL **Website** field. The weekly
     "enrich by hand" digest (`weekly_digest.py`) reads the link from there, so
     any daycare Clay can't complete still arrives with a clickable page link.
   - Put `violation_types` and `cited_date` into custom fields or the contact
     notes so your sequences can reference them.

6. **Run mode.** Set the table to auto-run on new rows so weekly POSTs enrich and
   sync without manual clicks.

## Add the secret

Repo → **Settings → Secrets and variables → Actions**:

| Secret | Value |
|---|---|
| `CLAY_WEBHOOK_URL` | the webhook URL from step 1 |

That's the only new secret. Everything else is unchanged from
`GOHIGHLEVEL_SETUP.md`.

## Test

```bash
# from the repo root
CLAY_WEBHOOK_URL="https://api.clay.com/.../webhook" python weekly_agent.py
```
With no GHL token this runs DRY-RUN and prints, per daycare, whether it would go
`Clay->GHL (enrich)` or `GHL direct`, plus the exact Clay payload — so you can
confirm routing before anything is sent.
