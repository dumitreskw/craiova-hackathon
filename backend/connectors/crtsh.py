"""crt.sh connector: subdomains and certificates from CT logs.

Passive — reads public certificate transparency logs, never touches the
target. Produces ``Domain`` entities (subdomains) and ``Certificate`` entities.
"""

from __future__ import annotations

from .base import (
    ConnectorResult,
    EntityCandidate,
    EntityType,
    RelationshipCandidate,
)
from ._http import get_client, retry

ENDPOINT = "https://crt.sh/"
MAX_SUBDOMAINS = 500  # cap so a huge cert history can't explode the graph


async def crtsh_subdomains(domain: str) -> ConnectorResult:
    domain = domain.strip().lower().rstrip(".")
    client = get_client()

    async def fetch():
        # crt.sh is slow and 502s often; give it longer and retry transients.
        r = await client.get(ENDPOINT, params={"q": f"%.{domain}", "output": "json"},
                             timeout=30.0)
        r.raise_for_status()
        return r

    resp = await retry(fetch)

    # crt.sh returns [] with 200, or occasionally an empty body.
    try:
        rows = resp.json()
    except ValueError:
        rows = []

    subdomains: set[str] = set()
    certs: dict[str, dict] = {}

    for row in rows:
        for name in str(row.get("name_value", "")).splitlines():
            name = name.strip().lower().lstrip("*.")
            if name.endswith(domain) and name != domain:
                subdomains.add(name)
        serial = row.get("serial_number")
        if serial and serial not in certs:
            certs[serial] = {
                "issuer": row.get("issuer_name"),
                "not_before": row.get("not_before"),
                "not_after": row.get("not_after"),
                "common_name": row.get("common_name"),
            }

    entities: list[EntityCandidate] = [EntityCandidate(EntityType.DOMAIN, domain)]
    rels: list[RelationshipCandidate] = []

    for sub in sorted(subdomains)[:MAX_SUBDOMAINS]:
        entities.append(EntityCandidate(EntityType.DOMAIN, sub, {"is_subdomain": True}))
        rels.append(RelationshipCandidate(
            "subdomain_of", EntityType.DOMAIN, sub, EntityType.DOMAIN, domain))

    for serial, meta in list(certs.items())[:200]:
        entities.append(EntityCandidate(EntityType.CERTIFICATE, str(serial), meta))
        rels.append(RelationshipCandidate(
            "secured_by", EntityType.DOMAIN, domain, EntityType.CERTIFICATE, str(serial)))

    return ConnectorResult(
        source="crtsh", passive=True,
        raw={"domain": domain, "subdomain_count": len(subdomains), "cert_count": len(certs),
             "subdomains": sorted(subdomains)[:MAX_SUBDOMAINS]},
        entities=entities, relationships=rels,
        note=None if subdomains else "no certificates found in CT logs",
    )
