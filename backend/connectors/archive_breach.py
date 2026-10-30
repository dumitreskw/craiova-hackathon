"""Web archive history and breach exposure from free / cheap public sources.

* ``wayback`` — Internet Archive CDX index for a domain (and its subdomains):
  how long it has existed online, hosts it used to have, and archived files
  that are often gone from the live site (PDFs, spreadsheets, backups,
  config files, admin and login paths). Passive: the archive is queried, not
  the target. Free, no key.
* ``hibp`` — Have I Been Pwned: which breaches an email address is in, with
  breach dates and the data classes exposed. Never returns passwords.
  Key: ``HIBP_API_KEY`` (Core 1 subscription, about US$4.39/month billed
  yearly, 10 requests/minute).
"""

from __future__ import annotations

import asyncio
import os
import re
from collections import Counter
from urllib.parse import urlparse

from ._http import get_client, retry
from .base import ConnectorResult, EntityCandidate, EntityType, RelationshipCandidate

CDX = "https://web.archive.org/cdx/search/cdx"
MAX_ROWS = 5000
_INTERESTING_EXT = re.compile(
    r"\.(pdf|docx?|xlsx?|pptx?|csv|sql|bak|old|zip|tar|gz|7z|env|cfg|conf|ini|log|json|xml|txt|"
    r"yml|yaml|key|pem)$", re.IGNORECASE)
_INTERESTING_PATH = re.compile(
    r"/(admin|administrator|wp-admin|login|signin|backup|old|dev|staging|test|api|graphql|"
    r"phpmyadmin|\.git|\.env|server-status|config)(/|$)", re.IGNORECASE)


async def wayback(domain: str) -> ConnectorResult:
    domain = domain.strip().lower().rstrip(".")

    async def fetch():
        r = await get_client().get(CDX, timeout=60.0, params={
            "url": domain, "matchType": "domain", "output": "json", "collapse": "urlkey",
            "fl": "timestamp,original,statuscode,mimetype", "limit": MAX_ROWS})
        r.raise_for_status()
        return r.json() if r.text.strip() else []

    rows = await retry(fetch, attempts=2, base_delay=2.0)
    rows = rows[1:] if rows and rows[0] and rows[0][0] == "timestamp" else rows
    if not rows:
        return ConnectorResult(source="wayback", passive=True, raw={"domain": domain, "captures": 0},
                               note="no archived captures")

    hosts: Counter[str] = Counter()
    years: Counter[str] = Counter()
    interesting: dict[str, dict] = {}
    first = min(r[0] for r in rows)
    last = max(r[0] for r in rows)
    for ts, original, status, mime in rows:
        p = urlparse(original if "://" in original else "http://" + original)
        host = (p.hostname or "").lower().removeprefix("www.")
        if host == domain or host.endswith("." + domain):
            hosts[host] += 1
        years[ts[:4]] += 1
        path = p.path or "/"
        if (_INTERESTING_EXT.search(path) or _INTERESTING_PATH.search(path)) and \
                len(interesting) < 200 and original not in interesting:
            interesting[original] = {
                "url": original, "first_capture": ts, "status": status, "mime": mime,
                "archived": f"https://web.archive.org/web/{ts}/{original}"}

    entities = [EntityCandidate(EntityType.DOMAIN, domain, {
        "archived_since": first[:8], "archived_until": last[:8]})]
    rels = []
    for host in sorted(h for h in hosts if h != domain)[:300]:
        entities.append(EntityCandidate(EntityType.DOMAIN, host, {"seen_in_archive": True}))
        rels.append(RelationshipCandidate("subdomain_of", EntityType.DOMAIN, host,
                                          EntityType.DOMAIN, domain))
    for item in list(interesting.values())[:60]:
        entities.append(EntityCandidate(EntityType.URL, item["archived"], {
            "title": f"archived {item['url']}", "original": item["url"],
            "first_capture": item["first_capture"]}, match_quality=0.9))
    return ConnectorResult(
        source="wayback", passive=True,
        raw={"domain": domain, "captures": len(rows), "truncated": len(rows) >= MAX_ROWS,
             "first_capture": first, "last_capture": last,
             "captures_per_year": dict(sorted(years.items())),
             "hosts": dict(hosts.most_common(100)),
             "interesting": list(interesting.values())},
        entities=entities, relationships=rels)


HIBP_API = "https://haveibeenpwned.com/api/v3"


async def hibp(email: str) -> ConnectorResult:
    email = email.strip().lower()
    key = os.environ.get("HIBP_API_KEY")
    if not key:
        return ConnectorResult(source="hibp", passive=True, raw={},
                               note="HIBP_API_KEY not set; skipped")
    client = get_client()
    for attempt in range(3):
        r = await client.get(f"{HIBP_API}/breachedaccount/{email}", timeout=20.0,
                             params={"truncateResponse": "false"},
                             headers={"hibp-api-key": key})
        if r.status_code != 429:
            break
        await asyncio.sleep(float(r.headers.get("retry-after", "6")) + attempt)
    if r.status_code == 404:
        return ConnectorResult(source="hibp", passive=True, raw={"email": email, "breaches": []},
                               entities=[EntityCandidate(EntityType.EMAIL, email)],
                               note="not found in any HIBP breach")
    if r.status_code in (401, 403):
        raise PermissionError(f"HIBP refused the key ({r.status_code})")
    r.raise_for_status()
    breaches = r.json()
    entities = [EntityCandidate(EntityType.EMAIL, email)]
    rels = []
    summary = []
    for b in breaches:
        name = (b.get("Name") or "").lower()
        classes = b.get("DataClasses") or []
        summary.append({"name": b.get("Name"), "title": b.get("Title"), "domain": b.get("Domain"),
                        "breach_date": b.get("BreachDate"), "data_classes": classes,
                        "verified": b.get("IsVerified"), "sensitive": b.get("IsSensitive")})
        entities.append(EntityCandidate(EntityType.BREACH, name, {
            "title": b.get("Title"), "breach_date": b.get("BreachDate"),
            "data_classes": classes, "breached_site": b.get("Domain"),
            "verified": b.get("IsVerified")}))
        rels.append(RelationshipCandidate("appears_in_breach", EntityType.EMAIL, email,
                                          EntityType.BREACH, name, {
                                              "source": "hibp",
                                              "credential_exposed": "Passwords" in classes}))
    return ConnectorResult(source="hibp", passive=True,
                           raw={"email": email, "breaches": summary},
                           entities=entities, relationships=rels)

