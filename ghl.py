"""Minimal GoHighLevel (LeadConnector) API v2 client.

Only the calls the weekly agent needs: upsert a contact, add tags, add a note,
and enroll the contact in a workflow. Auth is a Bearer token -- either a
Private Integration token (Settings -> Private Integrations) or an OAuth access
token. All v2 requests require the `Version` header.

Docs: https://highlevel.stoplight.io/docs/integrations
"""

import time
import requests

API_BASE = "https://services.leadconnectorhq.com"
API_VERSION = "2021-07-28"


class GHLError(RuntimeError):
    pass


class GHL:
    def __init__(self, token, location_id, timeout=30, max_retries=4):
        if not token or not location_id:
            raise GHLError("GHL token and location_id are required.")
        self.location_id = location_id
        self.timeout = timeout
        self.max_retries = max_retries
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {token}",
            "Version": API_VERSION,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def _req(self, method, path, **kw):
        url = f"{API_BASE}{path}"
        for attempt in range(self.max_retries):
            r = self.s.request(method, url, timeout=self.timeout, **kw)
            if r.status_code == 429 or r.status_code >= 500:
                # rate limited / transient: exponential backoff
                time.sleep(2 ** attempt)
                continue
            if not r.ok:
                raise GHLError(f"{method} {path} -> {r.status_code}: {r.text[:400]}")
            return r.json() if r.text else {}
        raise GHLError(f"{method} {path} failed after {self.max_retries} retries")

    # -- contacts -----------------------------------------------------------
    def upsert_contact(self, *, name=None, phone=None, email=None, address=None,
                       city=None, state=None, postal_code=None, source=None,
                       tags=None, custom_fields=None, company_name=None,
                       website=None):
        """Create or update a contact (dedupes by email/phone within the location).
        Returns (contact_id, existing_tags)."""
        body = {"locationId": self.location_id}
        if name:         body["name"] = name
        if company_name: body["companyName"] = company_name
        if phone:        body["phone"] = phone
        if email:        body["email"] = email
        if address:      body["address1"] = address
        if city:         body["city"] = city
        if state:        body["state"] = state
        if postal_code:  body["postalCode"] = str(postal_code)
        if website:      body["website"] = website
        if source:       body["source"] = source
        if tags:         body["tags"] = tags
        if custom_fields:body["customFields"] = custom_fields
        data = self._req("POST", "/contacts/upsert", json=body)
        contact = data.get("contact", data)
        return contact.get("id"), contact.get("tags", []) or []

    def add_tags(self, contact_id, tags):
        return self._req("POST", f"/contacts/{contact_id}/tags",
                         json={"tags": tags})

    def add_note(self, contact_id, body):
        return self._req("POST", f"/contacts/{contact_id}/notes",
                         json={"body": body})

    # -- workflows ----------------------------------------------------------
    def add_to_workflow(self, contact_id, workflow_id):
        return self._req("POST",
                         f"/contacts/{contact_id}/workflow/{workflow_id}",
                         json={})

    # -- reading ------------------------------------------------------------
    def list_contacts(self, limit=100, max_pages=200):
        """Return all contacts in the location (paginated). Each dict includes
        tags, email, phone, dateAdded, etc."""
        out = []
        params = {"locationId": self.location_id, "limit": limit}
        for _ in range(max_pages):
            data = self._req("GET", "/contacts/", params=params)
            batch = data.get("contacts", []) or []
            out.extend(batch)
            meta = data.get("meta", {}) or {}
            start_after_id = meta.get("startAfterId")
            start_after = meta.get("startAfter")
            if not batch or not start_after_id:
                break
            params = {"locationId": self.location_id, "limit": limit,
                      "startAfterId": start_after_id, "startAfter": start_after}
        return out
