"""INTERPOL Notices connector — Red (and UN) notices.

Passive. Queries INTERPOL's public Notices API
(ws-public.interpol.int/notices/v1), the same service the interpol.int site
uses. Red Notices are international wanted-person requests; most are sought by
European member states, so this complements the EU Most Wanted list.

Note: INTERPOL's edge sometimes returns 403 to server-side clients (WAF / geo).
When that happens the connector returns a ``note`` instead of raising, so a
deep-analysis run isn't derailed — run it from an allowed host or via the
proxy the platform routes search traffic through. We do not spoof browser
identity to get around the block.

Every hit is an **unverified, name-based possible match** (see _legal.py).
"""

from __future__ import annotations

import httpx

from .base import ConnectorResult, EntityType
from ._http import get_client
from ._legal import UNVERIFIED_NOTE, legal_hit

API = "https://ws-public.interpol.int/notices/v1"
RESULTS = 20


def _split_name(query: str) -> dict[str, str]:
    """INTERPOL matches on family name + forename. Best-effort split."""
    q = query.strip()
    if "," in q:  # "SURNAME, Forename"
        surname, _, forename = q.partition(",")
        return {"name": surname.strip(), "forename": forename.strip()}
    parts = q.split()
    if len(parts) >= 2:
        return {"forename": parts[0], "name": " ".join(parts[1:])}
    return {"name": q}


async def interpol_notices(name: str, notice_type: str = "red") -> ConnectorResult:
    """Search INTERPOL notices. ``notice_type`` is 'red' or 'un'."""
    notice_type = notice_type.lower()
    if notice_type not in ("red", "un"):
        notice_type = "red"
    client = get_client()
    params = {**_split_name(name), "resultPerPage": str(RESULTS)}

    try:
        resp = await client.get(f"{API}/{notice_type}", params=params,
                                headers={"Accept": "application/json"})
    except (httpx.TransportError, httpx.TimeoutException) as e:
        return ConnectorResult(source="interpol", passive=True,
                               raw={"query": name, "error": str(e)},
                               note=f"INTERPOL unreachable: {e}")

    if resp.status_code in (403, 451):
        return ConnectorResult(
            source="interpol", passive=True,
            raw={"query": name, "status": resp.status_code},
            note="INTERPOL API blocked this host (403/451) — run from an allowed "
                 "host or via proxy; not retried to avoid hammering the edge.")
    resp.raise_for_status()
    data = resp.json()

    notices = data.get("_embedded", {}).get("notices", []) or []
    entities, rels = [], []
    simplified = []
    for n in notices:
        full_name = " ".join(x for x in [n.get("forename"), n.get("name")] if x).strip()
        if not full_name:
            continue
        self_href = n.get("_links", {}).get("self", {}).get("href", "")
        nationalities = n.get("nationalities") or []
        dob = n.get("date_of_birth")
        simplified.append({
            "name": full_name, "entity_id": n.get("entity_id"),
            "nationalities": nationalities, "date_of_birth": dob, "url": self_href,
        })
        e, r = legal_hit(
            subject_name=full_name,
            subject_type=EntityType.PERSON,
            edge="appears_in_wanted_list",
            source_url=self_href or f"{API}/{notice_type}",
            source="interpol",
            attributes={
                "list": f"INTERPOL {notice_type.upper()} Notice",
                "title": full_name,
                "entity_id": n.get("entity_id"),
            },
            disambiguators={"date_of_birth": dob, "nationalities": nationalities},
        )
        entities += e
        rels += r

    note = UNVERIFIED_NOTE if simplified else "no matching INTERPOL notices"
    return ConnectorResult(
        source="interpol", passive=True,
        raw={"query": name, "notice_type": notice_type,
             "total": data.get("total"), "notices": simplified},
        entities=entities, relationships=rels, note=note,
    )
