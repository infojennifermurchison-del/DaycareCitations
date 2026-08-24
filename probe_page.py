"""Probe the HHS operator page to see if Texas Rising Star participation/rating
is fetchable there (the open dataset does not carry it)."""
import re
import requests

s = requests.Session()
s.headers["User-Agent"] = "Mozilla/5.0 (probe)"

PAGE = ("https://childcare.hhs.texas.gov/Public/OperationDetails"
        "?operationId=166018&resCareFlag=false")

print("=== Fetch operator page (ABC Word Academy, op_id 166018) ===")
try:
    r = s.get(PAGE, timeout=60)
    low = r.text.lower()
    print("status:", r.status_code, "| html length:", len(r.text))
    for kw in ("rising star", "star level", "texas rising", "quality rating",
               "trs", "star rating", "certif"):
        print(f"  contains '{kw}':", kw in low)
    apis = sorted(set(re.findall(r"[\"']([^\"']*api[^\"']*)[\"']", r.text, re.I)))[:40]
    print("  api-ish refs:", apis)
    srcs = sorted(set(re.findall(r"<script[^>]+src=[\"']([^\"']+)[\"']", r.text, re.I)))[:40]
    print("  script srcs:", srcs)
except Exception as e:
    print("page error:", e)

print("\n=== Try candidate HHS API endpoints ===")
for ep in [
    "https://childcare.hhs.texas.gov/api/OperationDetails/166018",
    "https://childcare.hhs.texas.gov/api/operations/166018",
    "https://childcare.hhs.texas.gov/Public/GetOperationDetails?operationId=166018",
    "https://childcare.hhs.texas.gov/api/OperationDetails?operationId=166018",
]:
    try:
        r = s.get(ep, timeout=30)
        marker = ""
        if r.status_code == 200 and ("star" in r.text.lower() or "rising" in r.text.lower()):
            marker = "   >>> CONTAINS TRS TEXT"
        print(ep, "->", r.status_code, "len", len(r.text), marker)
    except Exception as e:
        print(ep, "error", e)
