"""VirusTotal: reputation and passive DNS for a domain or IP.

Passive — VirusTotal's own data (scans by ~90 security engines, passive DNS,
WHOIS, certificates); nothing is sent to the target. Answers "is this domain
or IP known bad?" and "what has resolved to / from it?".

Key: ``VIRUSTOTAL_API_KEY``. The free public API allows 4 requests a minute
and 500 a day; each lookup here makes two (report + resolutions), so the
ledger quota is 240 lookups a day and 429s are waited out.
"""

from __future__ import annotations

import asyncio
import datetime
import ipaddress
import os

from ._http import get_client
from .base import ConnectorResult, EntityCandidate, EntityType, RelationshipCandidate

API = "https://www.virustotal.com/api/v3"


async def _get(path: str, key: str, params: dict | None = None) -> dict | None:
    client = get_client()
    for attempt in range(4):
        r = await client.get(API + path, params=params, timeout=25.0, headers={"x-apikey": key})
        if r.status_code == 429:  # public API: 4 requests a minute
            await asyncio.sleep(16 + attempt * 5)
            continue
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    raise RuntimeError("VirusTotal rate limit: still throttled after retries")


def _date(ts) -> str | None:
    return datetime.datetime.fromtimestamp(ts, datetime.UTC).date().isoformat() if ts else None


async def virustotal(value: str) -> ConnectorResult:
    key = os.environ.get("VIRUSTOTAL_API_KEY")
    if not key:
        return ConnectorResult(source="virustotal", passive=True, raw={},
                               note="VIRUSTOTAL_API_KEY not set; skipped")
    v = value.strip().lower().rstrip(".")
    try:
        ipaddress.ip_address(v)
        kind, etype = "ip_addresses", EntityType.IP
    except ValueError:
        kind, etype = "domains", EntityType.DOMAIN

    report = await _get(f"/{kind}/{v}", key)
    if report is None:
        return ConnectorResult(source="virustotal", passive=True, raw={"query": v},
                               note="not known to VirusTotal")
    a = (report.get("data") or {}).get("attributes") or {}
    stats = a.get("last_analysis_stats") or {}
    flagged = {eng: res.get("result") for eng, res in (a.get("last_analysis_results") or {}).items()
               if res.get("category") in ("malicious", "suspicious")}
    attrs = {"vt_reputation": a.get("reputation"), "vt_malicious": stats.get("malicious", 0),
             "vt_suspicious": stats.get("suspicious", 0), "vt_harmless": stats.get("harmless", 0),
             "vt_categories": sorted(set((a.get("categories") or {}).values()))[:8] or None,
             "vt_tags": a.get("tags") or None}
    if etype == EntityType.DOMAIN:
        attrs.update({"registrar": a.get("registrar"), "created": _date(a.get("creation_date"))})
    else:
        attrs.update({"asn": a.get("asn"), "as_owner": a.get("as_owner"),
                      "country": a.get("country"), "network": a.get("network")})
    entities = [EntityCandidate(etype, v, {k: x for k, x in attrs.items() if x not in (None, "")})]
    rels: list[RelationshipCandidate] = []

    res = await _get(f"/{kind}/{v}/resolutions", key, {"limit": 40})
    resolutions = []
    for item in (res or {}).get("data") or []:
        ra = item.get("attributes") or {}
        host, ip = (ra.get("host_name") or "").lower(), ra.get("ip_address")
        if not host or not ip:
            continue
        resolutions.append({"host": host, "ip": ip, "date": _date(ra.get("date"))})
        if etype == EntityType.DOMAIN and not (host == v or host.endswith("." + v)):
            continue
        entities += [EntityCandidate(EntityType.DOMAIN, host), EntityCandidate(EntityType.IP, ip)]
        rels.append(RelationshipCandidate("resolved_to_historically", EntityType.DOMAIN, host,
                                          EntityType.IP, ip, {"seen": _date(ra.get("date"))}))
    bad = stats.get("malicious", 0) + stats.get("suspicious", 0)
    return ConnectorResult(
        source="virustotal", passive=True,
        raw={"query": v, "last_analysis_stats": stats, "reputation": a.get("reputation"),
             "flagged_by": flagged, "categories": a.get("categories"),
             "resolutions": resolutions,
             "permalink": f"https://www.virustotal.com/gui/{'domain' if etype == EntityType.DOMAIN else 'ip-address'}/{v}"},
        entities=entities, relationships=rels,
        note=f"flagged malicious/suspicious by {bad} engines" if bad else None)
