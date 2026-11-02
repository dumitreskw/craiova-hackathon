"""Company officers by name (OpenCorporates): which companies a person runs.

Passive. OpenCorporates aggregates official company registers (UK, US
states, much of the EU; Romanian coverage is partial) including directors
and other officers. Searching a person's name returns every officer with that
name, each with their company, role, dates and, where the register publishes
it, nationality, occupation and birth month.

That is exactly the namesake problem: each officer is folded by the resolver
(app/resolver), which merges one into the subject only when the company is
already tied to them (a profile employer, a target company); the rest stay
separate same-name people for the verify agent's who's-who.

Needs ``OPENCORPORATES_API_TOKEN`` (the API no longer answers anonymous
searches); without it the connector skips cleanly.
"""

from __future__ import annotations

import os

from ._http import get_client
from ._legal import normalize_name
from .base import ConnectorResult, EntityCandidate, EntityType, RelationshipCandidate

API = "https://api.opencorporates.com/v0.4/officers/search"
PER_PAGE = 50


async def opencorporates_officers(name: str, jurisdiction: str | None = None) -> ConnectorResult:
    token = os.environ.get("OPENCORPORATES_API_TOKEN")
    if not token:
        return ConnectorResult(source="opencorporates", passive=True, raw={"query": name},
                               note="OPENCORPORATES_API_TOKEN not set; skipped")
    name = " ".join(name.strip().strip('"').split())
    params = {"q": name, "per_page": str(PER_PAGE), "api_token": token,
              "order": "score"}
    if jurisdiction:
        params["jurisdiction_code"] = jurisdiction.lower()
    r = await get_client().get(API, params=params, timeout=40.0)
    if r.status_code in (401, 403):
        raise PermissionError(f"OpenCorporates refused the search ({r.status_code})")
    r.raise_for_status()
    results = (r.json().get("results") or {})
    officers = [o.get("officer") or {} for o in results.get("officers") or []]
    entities, rels, listed = [], [], []
    for o in officers:
        company = o.get("company") or {}
        if not o.get("name") or not company.get("company_number"):
            continue
        jur = (company.get("jurisdiction_code") or "").lower()
        org_key = f"{jur}:{company['company_number']}".upper() if jur else company["company_number"]
        if jur == "gb":
            org_key = f"GB{company['company_number']}"   # same key as companies_house
        # Several officers can share a name in one result: key each by company.
        pkey = f"{normalize_name(o['name'])} @ {org_key}"
        listed.append({"name": o["name"], "position": o.get("position"),
                       "company": company.get("name"), "company_key": org_key,
                       "start": o.get("start_date"), "end": o.get("end_date")})
        entities.append(EntityCandidate(EntityType.ORGANIZATION, org_key, {
            k: v for k, v in {"legal_name": company.get("name"), "jurisdiction": jur,
                              "company_number": company.get("company_number"),
                              "registry_url": company.get("opencorporates_url")}.items() if v}))
        entities.append(EntityCandidate(EntityType.PERSON, pkey, {
            k: v for k, v in {"title": o["name"].title(), "nationality": o.get("nationality"),
                              "occupation": o.get("occupation"),
                              "date_of_birth": o.get("date_of_birth")}.items() if v},
            match_quality=0.9))
        rels.append(RelationshipCandidate("officer_of", EntityType.PERSON, pkey,
                                          EntityType.ORGANIZATION, org_key, {
            k: v for k, v in {"role": o.get("position"), "appointed": o.get("start_date"),
                              "resigned": o.get("end_date"), "inactive": o.get("inactive"),
                              "source_url": o.get("opencorporates_url")}.items()
            if v not in (None, "")}))
    total = results.get("total_count", len(officers))
    note = None if listed else "no officers with this name"
    if listed:
        note = (f"{total} officer record(s) named like {name!r}; each is its own person "
                "until a company ties one to the subject")
    return ConnectorResult(source="opencorporates", passive=True,
                           raw={"query": name, "total": total, "officers": listed},
                           entities=entities, relationships=rels, note=note)
