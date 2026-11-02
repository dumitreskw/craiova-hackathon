"""Romanian court cases (portal.just.ro) where a person or company is a party.

Passive and free: the Ministry of Justice's public SOAP service
(``portalquery.just.ro``, ``CautareDosare``) searches the case lists of every
court by party name. Each case gives the court (and so the city), the party's
role (reclamant, pârât, inculpat, creditor...), the subject matter, the stage
and the last hearing.

A party name is still only a name, so every match is an **unverified
candidate** like a wanted-list hit (_legal.py): its own node per case, linked
to the subject only by ``possible_match``. The court's city is the
disambiguator that usually settles it ("the Craiova court, the subject lives
in Craiova"). Common names return hundreds of cases: the newest
:data:`MAX_CASES` are kept as entities, every match is listed in the raw
payload, and the response is read incrementally with a size cap.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta

from ._http import get_client
from ._legal import NAME_MATCH_QUALITY, normalize_name
from ._names import matches_query
from .base import ConnectorResult, EntityCandidate, EntityType, RelationshipCandidate

API = "http://portalquery.just.ro/query.asmx"
NS = "portalquery.just.ro"
MAX_CASES = 25            # newest matching cases kept as entities
MAX_LISTED = 200          # matching cases listed in the raw payload
MAX_BYTES = 12_000_000    # stop reading a huge response (a big company's caseload)
YEARS_BACK = 15

_BAD_REF = re.compile(r"&#(?:x0*[0-8bcef]|x0*1[0-9a-f]|0*(?:[0-8]|1[124-9]|2[0-9]|3[01]));",
                      re.IGNORECASE)
_BAD_CHAR = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_CITY = re.compile(r"([A-Z][A-Z0-9]{2,})$")


_PARTY_TAIL = re.compile(r"\s+PRIN\s+|,\s*J\s?\d|,?\s*\b(?:CIF|CUI|CNP)\b|\s+-\s+|\(",
                         re.IGNORECASE)


def clean_party(name: str) -> str:
    """'ELECTRICA SA, J40/8974/2011, CIF RO 28909028, PRIN REPREZENTANT LEGAL X' ->
    'ELECTRICA SA': the party, without its registry numbers and representative."""
    head = _PARTY_TAIL.split(" ".join((name or "").split()), maxsplit=1)[0]
    return head.strip(" ,.;-") or " ".join((name or "").split())


def court_city(institutie: str) -> str | None:
    """'JudecatoriaCRAIOVA' -> 'Craiova'; 'TribunalulBUCURESTI' -> 'Bucuresti'."""
    if "BUCURESTI" in (institutie or ""):
        return "Bucuresti"
    m = _CITY.search(institutie or "")
    return m.group(1).title() if m else None


def _envelope(name: str, since: str) -> str:
    esc = (name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    return ('<?xml version="1.0" encoding="utf-8"?>'
            '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<soap:Body><CautareDosare xmlns="{NS}"><numeParte>{esc}</numeParte>'
            '<institutie xsi:nil="true"/>'
            f'<dataStart>{since}</dataStart></CautareDosare></soap:Body></soap:Envelope>')


def _text(el: ET.Element, tag: str) -> str:
    child = el.find(f"{{{NS}}}{tag}")
    return (child.text or "").strip() if child is not None else ""


def _case(el: ET.Element) -> dict:
    parti = [{"nume": " ".join(_text(p, "nume").split()), "calitate": _text(p, "calitateParte")}
             for p in el.iterfind(f"{{{NS}}}parti/{{{NS}}}DosarParte")]
    hearings = [{"data": _text(s, "data")[:10], "solutie": _text(s, "solutie"),
                 "sumar": _text(s, "solutieSumar")[:300]}
                for s in el.iterfind(f"{{{NS}}}sedinte/{{{NS}}}DosarSedinta")]
    hearings.sort(key=lambda h: h["data"], reverse=True)
    return {"numar": _text(el, "numar"), "data": _text(el, "data")[:10],
            "institutie": _text(el, "institutie"), "departament": _text(el, "departament"),
            "categorie": _text(el, "categorieCazNume") or _text(el, "categorieCaz"),
            "stadiu": _text(el, "stadiuProcesualNume") or _text(el, "stadiuProcesual"),
            "obiect": _text(el, "obiect"), "modificat": _text(el, "dataModificare")[:10],
            "parti": parti, "ultima_sedinta": hearings[0] if hearings else None}


async def _search(name: str) -> tuple[list[dict], bool]:
    """Cases naming ``name`` as a party; ``(cases, truncated)``."""
    since = (datetime.now(UTC) - timedelta(days=365 * YEARS_BACK)).strftime("%Y-%m-%dT00:00:00")
    parser = ET.XMLPullParser(events=("end",))
    cases: list[dict] = []
    read, truncated, buf = 0, False, ""
    async with get_client().stream(
            "POST", API, content=_envelope(name, since).encode("utf-8"), timeout=90.0,
            headers={"Content-Type": "text/xml; charset=utf-8",
                     "SOAPAction": f'"{NS}/CautareDosare"'}) as r:
        r.raise_for_status()
        async for chunk in r.aiter_text():
            read += len(chunk)
            buf += chunk
            cut = buf.rfind(">") + 1          # char refs never span a '>'
            parser.feed(_BAD_CHAR.sub(" ", _BAD_REF.sub(" ", buf[:cut])))
            buf = buf[cut:]
            for _event, el in parser.read_events():
                if el.tag == f"{{{NS}}}Dosar":
                    cases.append(_case(el))
                    el.clear()
            if read > MAX_BYTES:
                truncated = True
                break
    return cases, truncated


def _hits(name: str, cases: list[dict]) -> list[dict]:
    """One row per (case, matching party), newest first."""
    out = []
    for c in cases:
        for p in c["parti"]:
            if p["nume"] and matches_query(name, p["nume"]):
                out.append({**c, "party": clean_party(p["nume"]), "party_listed": p["nume"],
                            "role": p["calitate"]})
                break
    out.sort(key=lambda h: h["data"], reverse=True)
    return out


async def _court_records(name: str, etype: EntityType, source: str) -> ConnectorResult:
    name = " ".join(name.strip().strip('"').split())
    if len(name) < 4:
        raise ValueError("a party name of at least 4 characters is needed")
    cases, truncated = await _search(name)
    hits = _hits(name, cases)
    entities, rels = [], []
    for h in hits[:MAX_CASES]:
        case_key = f"just.ro:{h['numar']}@{h['institutie']}"
        city = court_city(h["institutie"])
        entities.append(EntityCandidate(EntityType.DOCUMENT, case_key, {
            "kind": "court_case", "title": f"Case {h['numar']} ({h['institutie']})",
            "case_number": h["numar"], "court": h["institutie"], "city": city,
            "section": h["departament"], "filed": h["data"], "category": h["categorie"],
            "stage": h["stadiu"], "subject_matter": h["obiect"],
            "last_hearing": h["ultima_sedinta"], "parties": len(h["parti"]),
            "lookup": f"portal.just.ro, case number {h['numar']}"}))
        entities.append(EntityCandidate(etype, normalize_name(h["party"]), {
            "title": h["party"].title(), "list": "Romanian court cases (portal.just.ro)",
            "unverified": True, "listing_url": case_key,
            "disambiguators": {k: v for k, v in {"court_city": city, "role": h["role"],
                                                 "case_filed": h["data"]}.items() if v}},
            match_quality=NAME_MATCH_QUALITY))
        rels.append(RelationshipCandidate(
            "party_in_case", etype, normalize_name(h["party"]), EntityType.DOCUMENT, case_key,
            {"unverified": True, "role": h["role"], "source": source}))
    listed = [{k: h[k] for k in ("numar", "institutie", "data", "categorie", "stadiu",
                                 "obiect", "party", "role")} for h in hits[:MAX_LISTED]]
    note = (f"{len(hits)} case(s) name a party matching {name!r}: name-only matches, "
            "unverified until the court city, role or case details tie one to the subject"
            if hits else "no court cases with a matching party")
    if len(hits) > MAX_CASES:
        note += f"; the newest {MAX_CASES} are in the graph, all are listed in the finding"
    if truncated:
        note += "; the response was cut at the size cap (very common name or large company)"
    return ConnectorResult(source=source, passive=True,
                           raw={"query": name, "cases_returned": len(cases),
                                "matches": len(hits), "truncated": truncated, "cases": listed},
                           entities=entities, relationships=rels, note=note)


async def court_records(name: str) -> ConnectorResult:
    """Court cases where a person is a party."""
    return await _court_records(name, EntityType.PERSON, "court_records")


async def court_records_org(name: str) -> ConnectorResult:
    """Court cases where a company is a party."""
    return await _court_records(name, EntityType.ORGANIZATION, "court_records_org")
