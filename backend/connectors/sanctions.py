"""Sanctions / PEP / watchlist screening via OpenSanctions.

Passive. OpenSanctions consolidates the EU, UN, OFAC, UK (and many national)
sanctions and wanted lists plus PEP data — strong European coverage. Its hosted
API requires a key: set ``OPENSANCTIONS_API_KEY``. Without one the connector
skips cleanly (returns a note) rather than failing; point it at a self-hosted
``yente`` instance with ``OPENSANCTIONS_API_URL`` if you prefer no key.

A match here is still name-led, so it is reported as an **unverified
candidate** like the wanted-list connectors (see _legal.py).
"""

from __future__ import annotations

import os

from .base import ConnectorResult, EntityType
from ._http import get_client
from ._legal import UNVERIFIED_NOTE, legal_hit

DEFAULT_API = "https://api.opensanctions.org"
ENTITY_PAGE = "https://www.opensanctions.org/entities/{id}/"

# OpenSanctions schema -> our entity type.
_SCHEMA_TO_TYPE = {
    "Person": EntityType.PERSON,
    "Organization": EntityType.ORGANIZATION,
    "Company": EntityType.ORGANIZATION,
    "LegalEntity": EntityType.ORGANIZATION,
}


async def sanctions(name: str) -> ConnectorResult:
    """Screen a name against OpenSanctions (sanctions, watchlists, PEP)."""
    api = os.environ.get("OPENSANCTIONS_API_URL", DEFAULT_API).rstrip("/")
    key = os.environ.get("OPENSANCTIONS_API_KEY")
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = f"ApiKey {key}"
    elif api == DEFAULT_API:
        return ConnectorResult(
            source="sanctions", passive=True,
            raw={"query": name},
            note="OPENSANCTIONS_API_KEY not set — skipped. Set it, or point "
                 "OPENSANCTIONS_API_URL at a self-hosted yente instance.")

    client = get_client()
    resp = await client.get(f"{api}/search/default",
                            params={"q": name, "limit": "10"}, headers=headers)
    if resp.status_code == 401:
        return ConnectorResult(source="sanctions", passive=True,
                               raw={"query": name, "status": 401},
                               note="OpenSanctions rejected the API key (401).")
    resp.raise_for_status()
    data = resp.json()
    results = data.get("results", []) or []

    entities, rels = [], []
    simplified = []
    for r in results:
        caption = r.get("caption")
        if not caption:
            continue
        schema = r.get("schema", "Person")
        etype = _SCHEMA_TO_TYPE.get(schema, EntityType.PERSON)
        props = r.get("properties", {}) or {}
        topics = props.get("topics", [])
        countries = props.get("country", []) or props.get("nationality", [])
        dob = props.get("birthDate", [])
        url = ENTITY_PAGE.format(id=r.get("id"))
        simplified.append({
            "caption": caption, "schema": schema, "id": r.get("id"),
            "datasets": r.get("datasets", []), "topics": topics,
            "countries": countries,
        })
        e, rel = legal_hit(
            subject_name=caption,
            subject_type=etype,
            edge="appears_in_sanctions",
            source_url=url,
            source="sanctions",
            attributes={
                "list": "OpenSanctions",
                "title": caption,
                "schema": schema,
                "topics": topics,  # e.g. sanction, crime, role.pep
                "datasets": r.get("datasets", []),
            },
            disambiguators={"birth_date": dob, "countries": countries},
        )
        entities += e
        rels += rel

    note = UNVERIFIED_NOTE if simplified else "no OpenSanctions matches"
    return ConnectorResult(
        source="sanctions", passive=True,
        raw={"query": name, "total": data.get("total"), "results": simplified},
        entities=entities, relationships=rels, note=note,
    )
