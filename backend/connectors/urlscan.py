"""urlscan.io connector: what public scans of a domain saw.

Passive — searches urlscan's index of scans other people already ran; nothing
is sent to the target. Each scan records the page URL, the IP and ASN that
served it, the server banner and a screenshot. Gives hosting history and
related hosts without touching the target. Free key (``URLSCAN_API_KEY``),
rate-limited.
"""

from __future__ import annotations

import os

from ._http import get_client, retry
from .base import ConnectorResult, EntityCandidate, EntityType, RelationshipCandidate

API = "https://urlscan.io/api/v1/search/"
MAX_RESULTS = 100


async def urlscan(domain: str) -> ConnectorResult:
    domain = domain.strip().lower().rstrip(".")
    key = os.environ.get("URLSCAN_API_KEY")
    if not key:
        return ConnectorResult(source="urlscan", passive=True, raw={},
                               note="URLSCAN_API_KEY not set; skipped")

    async def fetch():
        r = await get_client().get(API, headers={"API-Key": key}, timeout=20.0,
                                   params={"q": f"domain:{domain}", "size": MAX_RESULTS})
        r.raise_for_status()
        return r.json()

    data = await retry(fetch)
    scans = []
    entities = [EntityCandidate(EntityType.DOMAIN, domain)]
    rels: list[RelationshipCandidate] = []
    seen_ip: set[str] = set()
    seen_host: set[str] = set()
    for res in data.get("results", []):
        page, task = res.get("page") or {}, res.get("task") or {}
        scans.append({"time": task.get("time"), "url": page.get("url"), "ip": page.get("ip"),
                      "asn": page.get("asn"), "asnname": page.get("asnname"),
                      "country": page.get("country"), "server": page.get("server"),
                      "status": page.get("status"), "screenshot": res.get("screenshot")})
        host = (page.get("domain") or "").lower()
        if host and host not in seen_host and (host == domain or host.endswith("." + domain)):
            seen_host.add(host)
            entities.append(EntityCandidate(EntityType.DOMAIN, host))
            if host != domain:
                rels.append(RelationshipCandidate("subdomain_of", EntityType.DOMAIN, host,
                                                  EntityType.DOMAIN, domain))
        ip = page.get("ip")
        if ip and host and ip not in seen_ip:
            seen_ip.add(ip)
            entities.append(EntityCandidate(EntityType.IP, ip, {
                "asn": page.get("asn"), "asn_name": page.get("asnname"),
                "country": page.get("country")}))
            rels.append(RelationshipCandidate("resolved_to_historically", EntityType.DOMAIN,
                                              host, EntityType.IP, ip,
                                              {"seen": task.get("time")}))
    return ConnectorResult(
        source="urlscan", passive=True,
        raw={"domain": domain, "total": data.get("total"), "scans": scans},
        entities=entities, relationships=rels,
        note=None if scans else "no public scans of this domain")
