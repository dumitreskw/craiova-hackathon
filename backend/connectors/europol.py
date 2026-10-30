"""EU Most Wanted (ENFAST) connector — Europe's most-wanted fugitives.

Passive. ENFAST is the network of EU national fugitive-hunting units; its
public site (eumostwanted.eu) lists the fugitives each member state is looking
for, which is exactly the "European police / wanted lists" focus. The listing
is embedded in the homepage as a structured table, so one fetch gets every
current fugitive; we filter by name locally.

Every hit is an **unverified, name-based possible match** (see _legal.py).
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from .base import ConnectorResult, EntityType
from ._http import get_client, retry
from ._legal import UNVERIFIED_NOTE, legal_hit, normalize_name

BASE = "https://eumostwanted.eu"


def _field(row, suffix: str) -> str | None:
    el = row.select_one(f"div.views-field-{suffix} .field-content")
    return el.get_text(strip=True) if el else None


def _parse_fugitives(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    for row in soup.select("div.views-row"):
        name = _field(row, "title")
        if not name:
            continue
        path = _field(row, "view-node")
        out.append({
            "name": name,
            "nid": _field(row, "nid"),
            "detail_url": f"{BASE}{path}" if path else BASE,
            "requesting_country": _field(row, "field-enfast-country"),
            "country_code": _field(row, "field-country-code"),
            "probable_locations": _field(row, "field-propable-locations"),
            "reward": _field(row, "field-reward"),
        })
    return out


async def europol_wanted(name: str) -> ConnectorResult:
    """Search the EU Most Wanted (ENFAST) fugitive list for a name."""
    client = get_client()

    async def fetch():
        r = await client.get(f"{BASE}/")
        r.raise_for_status()
        return r

    resp = await retry(fetch)
    fugitives = _parse_fugitives(resp.text)

    needle = normalize_name(name)
    matches = [f for f in fugitives if needle in normalize_name(f["name"])]

    entities = []
    rels = []
    for f in matches:
        e, r = legal_hit(
            subject_name=f["name"],
            subject_type=EntityType.PERSON,
            edge="appears_in_wanted_list",
            source_url=f["detail_url"],
            source="europol",
            attributes={
                "list": "EU Most Wanted (ENFAST)",
                "title": f["name"],
                "requesting_country": f["requesting_country"],
                "reward": f["reward"],
            },
            disambiguators={
                "probable_locations": f["probable_locations"],
                "country_code": f["country_code"],
            },
        )
        entities += e
        rels += r

    note = UNVERIFIED_NOTE if matches else f"no match in EU Most Wanted ({len(fugitives)} listed)"
    return ConnectorResult(
        source="europol", passive=True,
        raw={"query": name, "total_listed": len(fugitives),
             "matches": matches},
        entities=entities, relationships=rels, note=note,
    )
