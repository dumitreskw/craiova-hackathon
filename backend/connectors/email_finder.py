"""Email discovery for a company domain: Hunter.io and Tomba.io.

Passive — both search their own indexes of addresses found on the public web;
nothing is sent to the target or to the mailboxes. They return addresses with
the person's name and role where known, the domain's address pattern, and the
pages each address was seen on.

Both are **metered** (Hunter ~50, Tomba ~25 searches a month on the keys in
use): the quick scan doesn't run them unless the investigation opts in, and
the ledger enforces the monthly quota.
"""

from __future__ import annotations

import os

from ._http import get_client
from .base import ConnectorResult, EntityCandidate, EntityType, RelationshipCandidate


def _people(source: str, domain: str, org_name: str | None, emails: list[dict],
            pattern: str | None, raw: dict) -> ConnectorResult:
    # The providers' organization name is often wrong (seen live: "Phyton" for
    # python.org), so it stays a label on the domain, not an Organization node;
    # registries (RDAP, ANAF...) are the source for who owns a domain.
    entities = [EntityCandidate(EntityType.DOMAIN, domain, {
        f"{source}_org_label": org_name, "email_pattern": pattern})]
    rels: list[RelationshipCandidate] = []
    for e in emails:
        addr = (e.get("email") or "").lower()
        if not addr:
            continue
        quality = max(0.3, min(1.0, (e.get("confidence") or 50) / 100))
        entities.append(EntityCandidate(EntityType.EMAIL, addr, {
            "kind": e.get("type"), "confidence_" + source: e.get("confidence"),
            "seen_on": e.get("sources", [])[:5]}, match_quality=quality))
        rels.append(RelationshipCandidate("uses_email", EntityType.DOMAIN, domain,
                                          EntityType.EMAIL, addr))
        name = " ".join(x for x in (e.get("first_name"), e.get("last_name")) if x).strip()
        if name and e.get("type") != "generic":
            pkey = " ".join(name.lower().split())
            entities.append(EntityCandidate(EntityType.PERSON, pkey, {
                "title": name, "position": e.get("position"),
                "department": e.get("department")}, match_quality=quality))
            rels.append(RelationshipCandidate("uses_email", EntityType.PERSON, pkey,
                                              EntityType.EMAIL, addr))
            rels.append(RelationshipCandidate(
                "affiliated_with", EntityType.PERSON, pkey, EntityType.DOMAIN, domain,
                {"role": e.get("position"), "basis": f"{source} address on this domain"}))
        for platform in ("linkedin", "twitter"):
            handle = e.get(platform)
            if handle and name:
                key = f"{platform}:{str(handle).rstrip('/').split('/')[-1].lower()}"
                entities.append(EntityCandidate(EntityType.SOCIAL_PROFILE, key,
                                                {"platform": platform, "url": handle},
                                                match_quality=quality))
                rels.append(RelationshipCandidate("has_profile", EntityType.PERSON,
                                                  " ".join(name.lower().split()),
                                                  EntityType.SOCIAL_PROFILE, key))
    return ConnectorResult(source=source, passive=True, raw=raw, entities=entities,
                           relationships=rels,
                           note=None if emails else "no addresses indexed for this domain")


async def hunter(domain: str) -> ConnectorResult:
    domain = domain.strip().lower().rstrip(".")
    key = os.environ.get("HUNTER_API_KEY")
    if not key:
        return ConnectorResult(source="hunter", passive=True, raw={},
                               note="HUNTER_API_KEY not set; skipped")
    r = await get_client().get("https://api.hunter.io/v2/domain-search", timeout=25.0,
                               params={"domain": domain, "api_key": key, "limit": 10})
    r.raise_for_status()
    data = r.json().get("data") or {}
    emails = [{"email": e.get("value"), "type": e.get("type"),
               "confidence": e.get("confidence"), "first_name": e.get("first_name"),
               "last_name": e.get("last_name"), "position": e.get("position"),
               "department": e.get("department"), "linkedin": e.get("linkedin"),
               "twitter": e.get("twitter"),
               "sources": [s.get("uri") for s in e.get("sources") or [] if s.get("uri")]}
              for e in data.get("emails") or []]
    raw = {"domain": domain, "organization": data.get("organization"),
           "pattern": data.get("pattern"), "emails": emails,
           "results": (r.json().get("meta") or {}).get("results")}
    return _people("hunter", domain, data.get("organization"), emails,
                   data.get("pattern"), raw)


async def tomba(domain: str) -> ConnectorResult:
    domain = domain.strip().lower().rstrip(".")
    key, secret = os.environ.get("TOMBA_API_KEY"), os.environ.get("TOMBA_API_SECRET")
    if not (key and secret):
        return ConnectorResult(source="tomba", passive=True, raw={},
                               note="TOMBA_API_KEY/TOMBA_API_SECRET not set; skipped")
    r = await get_client().get("https://api.tomba.io/v1/domain-search", timeout=25.0,
                               params={"domain": domain},
                               headers={"X-Tomba-Key": key, "X-Tomba-Secret": secret})
    r.raise_for_status()
    data = r.json().get("data") or {}
    org = data.get("organization") or {}
    emails = [{"email": e.get("email"), "type": e.get("type"), "confidence": e.get("score"),
               "first_name": e.get("first_name"), "last_name": e.get("last_name"),
               "position": e.get("position"), "department": e.get("department"),
               "linkedin": e.get("linkedin"), "twitter": e.get("twitter"),
               "sources": [s.get("uri") for s in e.get("sources") or [] if s.get("uri")]}
              for e in data.get("emails") or []]
    raw = {"domain": domain, "organization": org.get("organization"),
           "pattern": org.get("email_pattern") or org.get("pattern"), "emails": emails}
    return _people("tomba", domain, org.get("organization"), emails, raw["pattern"], raw)
