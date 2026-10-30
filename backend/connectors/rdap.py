"""RDAP connector: domain registration data via the IANA bootstrap.

Passive. RDAP is the structured successor to WHOIS; the IANA bootstrap service
redirects to the authoritative registry for each TLD. Produces registrar,
key dates, and any registrant org / contact emails the registry exposes
(many redact these under GDPR, which is expected — we just record what's there).
"""

from __future__ import annotations

from typing import Any

from .base import (
    ConnectorResult,
    EntityCandidate,
    EntityType,
    RelationshipCandidate,
)
from ._http import get_client

BOOTSTRAP = "https://rdap.org/domain/{domain}"


def _collect_emails(vcard: list[Any]) -> list[str]:
    emails: list[str] = []
    for entry in vcard:
        if isinstance(entry, list) and entry and entry[0] == "email":
            value = entry[-1]
            if isinstance(value, str) and "@" in value:
                emails.append(value.lower())
    return emails


async def rdap_domain(domain: str) -> ConnectorResult:
    domain = domain.strip().lower().rstrip(".")
    client = get_client()
    resp = await client.get(BOOTSTRAP.format(domain=domain))

    if resp.status_code == 404:
        return ConnectorResult(
            source="rdap", passive=True, raw={"domain": domain, "status": 404},
            note="no RDAP record (domain unregistered or registry has no RDAP)",
        )
    resp.raise_for_status()
    data = resp.json()

    entities: list[EntityCandidate] = [EntityCandidate(EntityType.DOMAIN, domain)]
    rels: list[RelationshipCandidate] = []

    attrs: dict[str, Any] = {}
    for event in data.get("events", []):
        action = event.get("eventAction")
        if action in ("registration", "expiration", "last changed"):
            attrs[action.replace(" ", "_")] = event.get("eventDate")

    registrar = None
    for entity in data.get("entities", []):
        roles = entity.get("roles", [])
        vcard = entity.get("vcardArray", [None, []])[1] if entity.get("vcardArray") else []
        name = None
        for item in vcard:
            if isinstance(item, list) and item and item[0] == "fn":
                name = item[-1]
        if "registrar" in roles and name:
            registrar = name
        if "registrant" in roles and name:
            entities.append(EntityCandidate(EntityType.ORGANIZATION, name.lower(),
                                            {"display_name": name}, match_quality=0.6))
            rels.append(RelationshipCandidate(
                "registered_by", EntityType.DOMAIN, domain,
                EntityType.ORGANIZATION, name.lower()))
        for email in _collect_emails(vcard):
            entities.append(EntityCandidate(EntityType.EMAIL, email, match_quality=0.8))
            rels.append(RelationshipCandidate(
                "uses_email", EntityType.DOMAIN, domain, EntityType.EMAIL, email))

    if registrar:
        attrs["registrar"] = registrar
    attrs["nameservers"] = [ns.get("ldhName", "").lower() for ns in data.get("nameservers", [])]
    attrs["status"] = data.get("status", [])
    entities[0].attributes.update(attrs)

    return ConnectorResult(
        source="rdap", passive=True,
        raw={"domain": domain, "rdap": data},
        entities=entities, relationships=rels,
    )
