"""DNS connector: resolve the common record types for a domain.

Passive — queries public resolvers, not the target's own hosts. Produces
``IPAddress`` entities (A/AAAA), related ``Domain`` entities (MX/NS/CNAME),
and ``Email`` entities parsed from SOA and DMARC/TXT records.
"""

from __future__ import annotations

import re

import dns.asyncresolver
import dns.exception

from .base import (
    ConnectorResult,
    EntityCandidate,
    EntityType,
    RelationshipCandidate,
)

RECORD_TYPES = ("A", "AAAA", "MX", "NS", "TXT", "SOA", "CNAME", "CAA")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


async def dns_lookup(domain: str) -> ConnectorResult:
    domain = domain.strip().lower().rstrip(".")
    resolver = dns.asyncresolver.Resolver()
    resolver.lifetime = 10.0

    records: dict[str, list[str]] = {}
    entities: list[EntityCandidate] = []
    rels: list[RelationshipCandidate] = []

    def add_ip(value: str) -> None:
        entities.append(EntityCandidate(EntityType.IP, value))
        rels.append(
            RelationshipCandidate(
                "resolves_to", EntityType.DOMAIN, domain, EntityType.IP, value
            )
        )

    def add_domain(value: str, edge: str) -> None:
        value = value.rstrip(".").lower()
        entities.append(EntityCandidate(EntityType.DOMAIN, value))
        rels.append(
            RelationshipCandidate(edge, EntityType.DOMAIN, domain, EntityType.DOMAIN, value)
        )

    def add_email(value: str) -> None:
        value = value.lower()
        entities.append(EntityCandidate(EntityType.EMAIL, value, match_quality=0.7))
        rels.append(
            RelationshipCandidate(
                "uses_email", EntityType.DOMAIN, domain, EntityType.EMAIL, value
            )
        )

    for rtype in RECORD_TYPES:
        try:
            answer = await resolver.resolve(domain, rtype)
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.exception.DNSException):
            continue

        values = [r.to_text() for r in answer]
        records[rtype] = values

        for value in values:
            if rtype in ("A", "AAAA"):
                add_ip(value)
            elif rtype == "MX":
                host = value.split()[-1]
                add_domain(host, "mail_handled_by")
            elif rtype == "NS":
                add_domain(value, "nameserver")
            elif rtype == "CNAME":
                add_domain(value, "alias_of")
            elif rtype == "SOA":
                # SOA rname encodes the zone contact as hostmaster.example.com.
                rname = value.split()[1].rstrip(".")
                if "." in rname:
                    local, _, dom = rname.partition(".")
                    add_email(f"{local}@{dom}")
            elif rtype == "TXT":
                for email in _EMAIL_RE.findall(value):
                    add_email(email)

    entities.append(EntityCandidate(EntityType.DOMAIN, domain))

    return ConnectorResult(
        source="dns",
        passive=True,
        raw={"domain": domain, "records": records},
        entities=entities,
        relationships=rels,
        note=None if records else "no DNS records resolved",
    )
