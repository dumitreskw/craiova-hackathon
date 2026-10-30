"""AlienVault OTX passive DNS: which hostnames and IPs a domain or IP has had.

Passive — historical DNS observations collected by OTX sensors; nothing is
sent to the target. For a domain: past IPs and hostnames under it (finds
subdomains certificate logs miss, and old hosting). For an IP: the domains
that pointed at it (neighbours on shared hosting, related infrastructure).
Key: ``OTX_API_KEY`` (free).
"""

from __future__ import annotations

import ipaddress
import os

from ._http import get_client, retry
from .base import ConnectorResult, EntityCandidate, EntityType, RelationshipCandidate

API = "https://otx.alienvault.com/api/v1/indicators"
MAX_RECORDS = 300


async def otx_passive_dns(value: str) -> ConnectorResult:
    value = value.strip().lower().rstrip(".")
    key = os.environ.get("OTX_API_KEY")
    if not key:
        return ConnectorResult(source="otx", passive=True, raw={},
                               note="OTX_API_KEY not set; skipped")
    try:
        ip = ipaddress.ip_address(value)
        kind = "IPv6" if ip.version == 6 else "IPv4"
    except ValueError:
        ip, kind = None, "domain"

    async def fetch():
        r = await get_client().get(f"{API}/{kind}/{value}/passive_dns", timeout=45.0,
                                   headers={"X-OTX-API-KEY": key})
        r.raise_for_status()
        return r.json()

    data = await retry(fetch)
    records = (data.get("passive_dns") or [])[:MAX_RECORDS]
    entities: list[EntityCandidate] = []
    rels: list[RelationshipCandidate] = []
    if ip is None:
        entities.append(EntityCandidate(EntityType.DOMAIN, value))
    else:
        entities.append(EntityCandidate(EntityType.IP, str(ip)))
    seen: set[tuple[str, str]] = set()
    for rec in records:
        host = (rec.get("hostname") or "").lower().rstrip(".")
        addr = (rec.get("address") or "").strip()
        if not host or not addr or (host, addr) in seen:
            continue
        seen.add((host, addr))
        # For a domain lookup keep only hosts under it; CNAME targets elsewhere
        # belong to providers.
        if ip is None and not (host == value or host.endswith("." + value)):
            continue
        entities.append(EntityCandidate(EntityType.DOMAIN, host))
        try:
            ipaddress.ip_address(addr)
        except ValueError:
            continue  # CNAME records carry a hostname here
        entities.append(EntityCandidate(EntityType.IP, addr, {"asn": rec.get("asn")}))
        rels.append(RelationshipCandidate(
            "resolved_to_historically", EntityType.DOMAIN, host, EntityType.IP, addr,
            {"first_seen": rec.get("first"), "last_seen": rec.get("last")}))
    return ConnectorResult(
        source="otx", passive=True,
        raw={"query": value, "count": data.get("count"), "records": records},
        entities=entities, relationships=rels,
        note=None if records else "no passive DNS records")
