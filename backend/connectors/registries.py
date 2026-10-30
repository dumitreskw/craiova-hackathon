"""Official company registries: who a company legally is.

All passive (government / standards-body APIs, nothing sent to the target):

* ``anaf`` — Romanian tax authority (ANAF), by CUI: legal name, trade-register
  number (J...), CAEN activity code, incorporation date, registered and fiscal
  addresses, VAT status, inactive / struck-off status. Free, no key.
* ``vies`` — EU VAT validation (VIES), by VAT number: valid or not, registered
  name and address, for any EU member state. Free, no key.
* ``gleif`` — Global LEI index, by legal name or LEI: legal and headquarters
  address, local registration number, entity status, ultimate parent. Free.
* ``companies_house`` — UK Companies House, by company number or exact name:
  company profile, officers (with roles and dates) and persons with
  significant control. Free key (``COMPANIES_HOUSE_API_KEY``).

People found here (officers, PSCs) become entities like any other discovered
person: recorded, but never screened automatically (see app/scan).
"""

from __future__ import annotations

import datetime
import os
import re

from ._http import get_client, retry
from .base import ConnectorResult, EntityCandidate, EntityType, RelationshipCandidate

_CUI = re.compile(r"^(?:RO)?\s*(\d{2,10})$", re.IGNORECASE)
_VAT = re.compile(r"^([A-Z]{2})\s*([0-9A-Z+*.]{2,14})$")
_LEI = re.compile(r"^[A-Z0-9]{18}[0-9]{2}$")
_GB = re.compile(r"^(?:GB|UK)?([0-9A-Z]{2}[0-9]{6})$", re.IGNORECASE)
ORG, PERSON, LOC = EntityType.ORGANIZATION, EntityType.PERSON, EntityType.LOCATION


def _norm(name: str) -> str:
    return " ".join(name.lower().split())


def _skip(source: str, note: str) -> ConnectorResult:
    return ConnectorResult(source=source, passive=True, raw={}, note=note)


# --- ANAF (Romania) ------------------------------------------------------------------

ANAF_API = "https://webservicesp.anaf.ro/api/PlatitorTvaRest/v9/tva"


def _addr(parts: list[str | None]) -> str:
    return ", ".join(p.strip() for p in parts if p and p.strip())


async def anaf(cui: str) -> ConnectorResult:
    m = _CUI.match(cui.strip())
    if not m:
        return _skip("anaf", "ANAF needs a Romanian CUI (digits, optional RO prefix)")
    number = int(m.group(1))

    async def fetch():
        r = await get_client().post(ANAF_API, timeout=25.0, json=[
            {"cui": number, "data": datetime.date.today().isoformat()}])
        r.raise_for_status()
        return r.json()

    data = await retry(fetch)
    found = (data.get("found") or [None])[0]
    if not found:
        return ConnectorResult(source="anaf", passive=True, raw=data,
                               note=f"CUI {number} not found at ANAF")
    g = found.get("date_generale") or {}
    tva = found.get("inregistrare_scop_Tva") or {}
    inactive = found.get("stare_inactiv") or {}
    seat = found.get("adresa_sediu_social") or {}
    key = f"RO{number}"
    status = g.get("stare_inregistrare") or ""
    attrs = {
        "legal_name": g.get("denumire"), "country": "RO", "cui": str(number),
        "registration_number": g.get("nrRegCom"), "caen": g.get("cod_CAEN"),
        "incorporated": g.get("data_inregistrare"), "legal_form": g.get("forma_juridica"),
        "ownership": g.get("forma_de_proprietate"), "registry_status": status,
        "vat_registered": bool(tva.get("scpTVA")),
        "inactive": bool(inactive.get("statusInactivi")),
        "struck_off": bool(inactive.get("dataRadiere")),
        "phone": g.get("telefon") or None,
    }
    entities = [EntityCandidate(ORG, key, {k: v for k, v in attrs.items() if v not in ("", None)})]
    rels = []
    addresses = {
        "registered_office": _addr([seat.get("sdenumire_Strada"), seat.get("snumar_Strada"),
                                    seat.get("sdetalii_Adresa"), seat.get("sdenumire_Localitate"),
                                    seat.get("sdenumire_Judet")]),
        "fiscal_address": g.get("adresa") or "",
    }
    for role, address in addresses.items():
        if address:
            entities.append(EntityCandidate(LOC, _norm(address), {"address": address,
                                                                  "country": "RO"}))
            rels.append(RelationshipCandidate("located_at", ORG, key, LOC, _norm(address),
                                              {"role": role}))
    note = None
    if attrs["inactive"] or attrs["struck_off"]:
        note = "company is flagged inactive or struck off at ANAF"
    return ConnectorResult(source="anaf", passive=True, raw=found, entities=entities,
                           relationships=rels, note=note)


# --- VIES (EU VAT) ---------------------------------------------------------------------

VIES_API = "https://ec.europa.eu/taxation_customs/vies/rest-api/ms/{cc}/vat/{num}"


async def vies(vat: str) -> ConnectorResult:
    v = vat.strip().upper().replace(" ", "")
    m = _VAT.match(v)
    if m:
        cc, num = m.group(1), m.group(2)
    elif v.isdigit():
        cc, num = "RO", v  # a bare number is most often a Romanian CUI here
    else:
        return _skip("vies", "VIES needs a VAT number such as RO14399840 or DE123456789")
    cc = "EL" if cc == "GR" else cc

    async def fetch():
        r = await get_client().get(VIES_API.format(cc=cc, num=num), timeout=25.0)
        r.raise_for_status()
        return r.json()

    data = await retry(fetch)
    valid = bool(data.get("isValid"))
    key = f"{cc}{num}"
    attrs = {"vat_number": key, "vat_valid": valid, "country": cc}
    name = (data.get("name") or "").strip()
    if name and name != "---":
        attrs["legal_name"] = name
    entities = [EntityCandidate(ORG, key, attrs)]
    rels = []
    address = " ".join((data.get("address") or "").split())
    if address and address != "---":
        entities.append(EntityCandidate(LOC, _norm(address), {"address": address, "country": cc}))
        rels.append(RelationshipCandidate("located_at", ORG, key, LOC, _norm(address),
                                          {"role": "vat_address"}))
    return ConnectorResult(source="vies", passive=True, raw=data, entities=entities,
                           relationships=rels,
                           note=None if valid else f"{key} is not a valid VAT number in VIES "
                                                   f"({data.get('userError')})")


# --- GLEIF (LEI) -----------------------------------------------------------------------

GLEIF_API = "https://api.gleif.org/api/v1"


def _gleif_address(a: dict | None) -> str:
    a = a or {}
    return _addr([*(a.get("addressLines") or []), a.get("city"), a.get("region"),
                  a.get("postalCode"), a.get("country")])


async def gleif(name_or_lei: str) -> ConnectorResult:
    q = name_or_lei.strip()
    client = get_client()

    async def fetch(url: str, params: dict | None = None):
        r = await client.get(url, params=params, timeout=25.0,
                             headers={"Accept": "application/vnd.api+json"})
        if r.status_code == 404:
            return {}
        r.raise_for_status()
        return r.json()

    if _LEI.match(q.upper()):
        data = await retry(lambda: fetch(f"{GLEIF_API}/lei-records/{q.upper()}"))
        records = [data["data"]] if data.get("data") else []
    else:
        data = await retry(lambda: fetch(f"{GLEIF_API}/lei-records", {
            "filter[entity.legalName]": q, "page[size]": 5}))
        records = data.get("data") or []
        if not records:  # fuzzy endpoint for near-misses in punctuation/legal form
            data = await retry(lambda: fetch(f"{GLEIF_API}/fuzzycompletions", {
                "field": "entity.legalName", "q": q}))
            names = [d.get("attributes", {}).get("value") for d in data.get("data") or []]
            return ConnectorResult(source="gleif", passive=True,
                                   raw={"query": q, "suggestions": names[:10]},
                                   note="no exact LEI match; closest names in raw.suggestions")

    entities, rels, out = [], [], []
    for rec in records[:5]:
        at = rec.get("attributes") or {}
        ent = at.get("entity") or {}
        lei = at.get("lei")
        legal = (ent.get("legalName") or {}).get("name") or ""
        key = _norm(legal)
        attrs = {"legal_name": legal, "lei": lei, "lei_status": (at.get("registration") or {}).get("status"),
                 "entity_status": ent.get("status"), "jurisdiction": ent.get("jurisdiction"),
                 "registration_number": ent.get("registeredAs"),
                 "legal_form_code": (ent.get("legalForm") or {}).get("id")}
        entities.append(EntityCandidate(ORG, key, {k: v for k, v in attrs.items() if v}))
        # The local registration number ties the LEI record to the national
        # registry entry (ANAF for RO, Companies House for GB).
        reg, juris = (ent.get("registeredAs") or "").replace(" ", ""), ent.get("jurisdiction") or ""
        local = (f"RO{re.sub(r'[^0-9]', '', reg)}" if juris == "RO" and re.sub(r"[^0-9]", "", reg)
                 else f"GB{reg.upper()}" if juris.startswith("GB") and _GB.match(reg) else None)
        if local:
            entities.append(EntityCandidate(ORG, local, {"lei": lei}))
            rels.append(RelationshipCandidate("same_as", ORG, key, ORG, local,
                                              {"basis": "GLEIF registeredAs"}))
        seen_addr: set[str] = set()
        for role, field in (("legal_address", "legalAddress"),
                            ("headquarters", "headquartersAddress")):
            address = _gleif_address(ent.get(field))
            if address and _norm(address) not in seen_addr:
                seen_addr.add(_norm(address))
                entities.append(EntityCandidate(LOC, _norm(address), {"address": address}))
                rels.append(RelationshipCandidate("located_at", ORG, key, LOC, _norm(address),
                                                  {"role": role}))
        parent = await retry(lambda lei=lei: fetch(f"{GLEIF_API}/lei-records/{lei}/ultimate-parent"))
        pdata = (parent or {}).get("data")
        if pdata:
            pent = (pdata.get("attributes") or {}).get("entity") or {}
            pname = (pent.get("legalName") or {}).get("name") or ""
            if pname and _norm(pname) != key:
                entities.append(EntityCandidate(ORG, _norm(pname), {
                    "legal_name": pname, "lei": pdata["attributes"].get("lei"),
                    "jurisdiction": pent.get("jurisdiction")}))
                rels.append(RelationshipCandidate("owned_by", ORG, key, ORG, _norm(pname),
                                                  {"relation": "ultimate parent (GLEIF)"}))
        out.append({"lei": lei, "legal_name": legal, "jurisdiction": ent.get("jurisdiction"),
                    "registered_as": ent.get("registeredAs"), "status": ent.get("status"),
                    "ultimate_parent": ((pdata or {}).get("attributes") or {}).get("lei")})
    return ConnectorResult(source="gleif", passive=True, raw={"query": q, "records": out},
                           entities=entities, relationships=rels,
                           note=None if out else "no LEI record")


# --- Companies House (UK) ----------------------------------------------------------------

CH_API = "https://api.company-information.service.gov.uk"


async def companies_house(number_or_name: str) -> ConnectorResult:
    key_env = os.environ.get("COMPANIES_HOUSE_API_KEY")
    if not key_env:
        return _skip("companies_house", "COMPANIES_HOUSE_API_KEY not set; skipped")
    client = get_client()
    auth = (key_env, "")

    async def get(path: str, params: dict | None = None):
        r = await client.get(CH_API + path, params=params, auth=auth, timeout=25.0)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    q = number_or_name.strip()
    m = _GB.match(q.replace(" ", ""))
    number = m.group(1).upper() if m else None
    if number is None:
        found = await retry(lambda: get("/search/companies", {"q": q, "items_per_page": 10}))
        items = (found or {}).get("items") or []
        exact = [i for i in items if _norm(i.get("title", "")) == _norm(q)]
        if len(exact) != 1:
            return ConnectorResult(
                source="companies_house", passive=True,
                raw={"query": q, "candidates": [{"number": i.get("company_number"),
                                                 "name": i.get("title"),
                                                 "status": i.get("company_status")}
                                                for i in items]},
                note="no single exact name match; pick a company number from raw.candidates")
        number = exact[0]["company_number"]

    profile = await retry(lambda: get(f"/company/{number}"))
    if not profile:
        return ConnectorResult(source="companies_house", passive=True, raw={"number": number},
                               note=f"company {number} not found")
    officers = await retry(lambda: get(f"/company/{number}/officers", {"items_per_page": 100}))
    pscs = await retry(lambda: get(f"/company/{number}/persons-with-significant-control"))
    key = f"GB{number}"
    office = profile.get("registered_office_address") or {}
    address = _addr([office.get("address_line_1"), office.get("address_line_2"),
                     office.get("locality"), office.get("postal_code"), office.get("country")])
    entities = [EntityCandidate(ORG, key, {k: v for k, v in {
        "legal_name": profile.get("company_name"), "country": "GB", "company_number": number,
        "registry_status": profile.get("company_status"), "legal_form": profile.get("type"),
        "incorporated": profile.get("date_of_creation"), "dissolved": profile.get("date_of_cessation"),
        "sic_codes": profile.get("sic_codes"),
        "has_insolvency_history": profile.get("has_insolvency_history"),
        "has_charges": profile.get("has_charges")}.items() if v not in (None, "", [])})]
    rels = []
    if address:
        entities.append(EntityCandidate(LOC, _norm(address), {"address": address, "country": "GB"}))
        rels.append(RelationshipCandidate("located_at", ORG, key, LOC, _norm(address),
                                          {"role": "registered_office"}))
    for o in (officers or {}).get("items") or []:
        name = o.get("name") or ""
        if not name:
            continue
        pkey = _norm(_ch_name(name))
        etype = ORG if o.get("officer_role", "").startswith("corporate") else PERSON
        entities.append(EntityCandidate(etype, pkey, {k: v for k, v in {
            "title": _ch_name(name), "nationality": o.get("nationality"),
            "country_of_residence": o.get("country_of_residence"),
            "occupation": o.get("occupation")}.items() if v}, match_quality=0.9))
        rels.append(RelationshipCandidate("officer_of", etype, pkey, ORG, key, {
            "role": o.get("officer_role"), "appointed": o.get("appointed_on"),
            "resigned": o.get("resigned_on")}))
    for p in (pscs or {}).get("items") or []:
        name = p.get("name") or ""
        if not name:
            continue
        pkey = _norm(_ch_name(name))
        etype = PERSON if "individual" in (p.get("kind") or "") else ORG
        entities.append(EntityCandidate(etype, pkey, {"title": _ch_name(name)}, match_quality=0.9))
        rels.append(RelationshipCandidate("controls", etype, pkey, ORG, key, {
            "natures_of_control": p.get("natures_of_control"), "notified": p.get("notified_on"),
            "ceased": p.get("ceased_on")}))
    return ConnectorResult(
        source="companies_house", passive=True,
        raw={"profile": profile, "officers": (officers or {}).get("items") or [],
             "pscs": (pscs or {}).get("items") or []},
        entities=entities, relationships=rels)


def _ch_name(name: str) -> str:
    """Companies House writes officers as "SURNAME, Forenames"; turn into "Forenames Surname"."""
    if "," in name:
        last, first = name.split(",", 1)
        return f"{first.strip()} {last.strip().title()}".strip()
    return name.strip()
