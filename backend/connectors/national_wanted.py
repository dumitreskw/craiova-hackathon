"""National wanted / fugitive lists.

Passive. A dispatcher over per-country adapters (same shape as dork_search's
engines): each adapter searches one country's official wanted list by name and
returns normalized rows. ``national_wanted`` runs the requested adapters,
merges, and turns each row into an **unverified, name-based possible match**
via _legal.py (Person candidate + official-listing URL for evidence).

Reality of national police sites: most are hostile to direct access (WAF 403,
geo-blocks, broken TLS chains, or JS-only rendering). We never weaken TLS or
spoof browser identity to get in; an adapter that can't reach its source
returns a clear note and the run continues. Adapters marked ``verified`` parse
real responses today; the rest are wired and will work from a host that can
reach them (the VPS, or via the platform's proxy) once their parser is
finalized against live HTML.

Add a country by writing an ``async def adapter(name) -> list[Hit]`` and
registering it in ``ADAPTERS``.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .base import ConnectorResult, EntityType
from ._http import USER_AGENT, get_client
from ._legal import UNVERIFIED_NOTE, legal_hit
from ._names import matches_query
from ._tls import context_with_intermediates


@dataclass(slots=True)
class Hit:
    name: str
    country: str          # ISO-ish label of the requesting country
    source: str           # adapter name, e.g. "fbi"
    url: str              # official listing (evidence)
    list_name: str
    disambiguators: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)


class SourceUnavailable(Exception):
    """Adapter reached its source but was blocked/unreachable (not a bug)."""


# --- United States: FBI Wanted (clean public JSON API; verified) -------------

async def _fbi(name: str) -> list[Hit]:
    client = get_client()
    resp = await client.get(
        "https://api.fbi.gov/wanted/v1/list",
        params={"title": name, "pageSize": "20"},
        headers={"Accept": "application/json"},
    )
    resp.raise_for_status()
    hits: list[Hit] = []
    for item in resp.json().get("items", []):
        title = item.get("title")
        if not title:
            continue
        hits.append(Hit(
            name=title, country="United States", source="fbi",
            url=item.get("url") or "https://www.fbi.gov/wanted",
            list_name="FBI Wanted" + (
                f" — {item['subjects'][0]}" if item.get("subjects") else ""),
            disambiguators={
                "nationality": item.get("nationality"),
                "dates_of_birth_used": item.get("dates_of_birth_used"),
                "place_of_birth": item.get("place_of_birth"),
                "aliases": item.get("aliases"),
            },
            extra={"uid": item.get("uid"), "reward": item.get("reward_text"),
                   "field_offices": item.get("field_offices")},
        ))
    return hits


# --- European national lists (wired; blocked from many hosts) ----------------
# These return a clear SourceUnavailable from environments that can't reach them
# (WAF 403, or broken TLS chain for the RO site). Their HTML parsers must be
# finalized against live responses on a reachable host before flipping
# ``verified`` to True in SOURCES below.

RO_BASE = "https://www.politiaromana.ro"
RO_LIST = f"{RO_BASE}/ro/persoane-urmarite"
RO_MAX_PAGES = 15  # safety cap on pagination
# politiaromana.ro omits the Sectigo intermediate; we add it (certs/, see _tls).
RO_INTERMEDIATE = "sectigo_pub_server_auth_ca_dv_r36.pem"


def _ro_parse_page(html: str) -> tuple[list[dict], str | None]:
    """Return (persons, next_page_url) for one listing page."""
    soup = BeautifulSoup(html, "html.parser")
    persons: list[dict] = []
    next_url: str | None = None
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        if "următoare" in text.lower() or "urmatoare" in text.lower():
            next_url = urljoin(RO_BASE, href)
            continue
        # A person link has a slug segment after .../persoane-urmarite/
        m = re.search(r"/ro/persoane-urmarite/([^/?#]+)$", href)
        if m and text and text.lower() != "persoane urmarite":
            slug = m.group(1)
            dob = slug.rsplit("-", 1)[-1] if "-" in slug else None
            persons.append({
                "name": text,
                "url": urljoin(RO_BASE, href),
                "dob_code": dob if (dob and dob.isdigit()) else None,
            })
    return persons, next_url


async def _romania(name: str) -> list[Hit]:
    """Poliția Română — persoane urmărite (wanted persons).

    Verified live. Paginates the public wanted list and filters by name. The
    detail-page slug encodes a birth-date code we keep as a disambiguator.
    """
    try:
        ctx = context_with_intermediates(RO_INTERMEDIATE)
    except FileNotFoundError as e:
        raise SourceUnavailable(f"Poliția Română: {e}") from e

    needle = " ".join(name.strip().lower().split())
    collected: dict[str, dict] = {}
    try:
        async with httpx.AsyncClient(
            verify=ctx, timeout=25.0, follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            url: str | None = RO_LIST
            for _ in range(RO_MAX_PAGES):
                if not url:
                    break
                resp = await client.get(url)
                resp.raise_for_status()
                persons, url = _ro_parse_page(resp.text)
                for p in persons:
                    collected.setdefault(p["url"], p)
    except (httpx.TransportError, httpx.TimeoutException) as e:
        raise SourceUnavailable(f"Poliția Română unreachable: {e}") from e

    hits: list[Hit] = []
    for p in collected.values():
        if matches_query(needle, p["name"]):
            hits.append(Hit(
                name=p["name"], country="Romania", source="politiaromana",
                url=p["url"], list_name="Poliția Română — persoane urmărite",
                disambiguators={"dob_code": p["dob_code"]},
            ))
    return hits


async def _belgium(name: str) -> list[Hit]:
    raise SourceUnavailable(
        "Police Fédérale (politie.be most-wanted): WAF-blocks server clients "
        "(403) — enable from an allowed host or via proxy.")


async def _netherlands(name: str) -> list[Hit]:
    raise SourceUnavailable(
        "Politie.nl (gezocht): WAF-blocks server clients (403) — enable from "
        "an allowed host or via proxy.")


@dataclass(frozen=True, slots=True)
class Source:
    adapter: object
    country: str
    verified: bool  # True = parses real responses today


SOURCES: dict[str, Source] = {
    "fbi": Source(_fbi, "United States", True),
    "ro": Source(_romania, "Romania", True),
    "be": Source(_belgium, "Belgium", False),
    "nl": Source(_netherlands, "Netherlands", False),
}

ADAPTERS = {k: v.adapter for k, v in SOURCES.items()}


async def national_wanted(name: str, countries: list[str] | None = None) -> ConnectorResult:
    """Search national wanted lists for a person name.

    ``countries`` restricts which adapters run (e.g. ["fbi", "ro"]); default is
    all. Hits are unverified, name-based possible matches.
    """
    wanted = countries or list(SOURCES)
    used, unknown = [], []
    tasks = []
    for key in wanted:
        if key not in SOURCES:
            unknown.append(key)
            continue
        used.append(key)
        tasks.append(SOURCES[key].adapter(name))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    entities, rels, simplified = [], [], []
    unavailable: dict[str, str] = {}
    errors: dict[str, str] = {}

    for key, outcome in zip(used, results):
        if isinstance(outcome, SourceUnavailable):
            unavailable[key] = str(outcome)
            continue
        if isinstance(outcome, (httpx.HTTPStatusError, httpx.TransportError,
                                httpx.TimeoutException)):
            errors[key] = f"{type(outcome).__name__}: {outcome}"
            continue
        if isinstance(outcome, Exception):
            errors[key] = f"{type(outcome).__name__}: {outcome}"
            continue
        for hit in outcome:
            simplified.append({"name": hit.name, "country": hit.country,
                               "source": hit.source, "url": hit.url,
                               "list": hit.list_name, **hit.extra})
            e, r = legal_hit(
                subject_name=hit.name,
                subject_type=EntityType.PERSON,
                edge="appears_in_wanted_list",
                source_url=hit.url,
                source="national_wanted",
                attributes={"list": hit.list_name, "title": hit.name,
                            "requesting_country": hit.country, **hit.extra},
                disambiguators=hit.disambiguators,
            )
            entities += e
            rels += r

    note_bits = []
    if simplified:
        note_bits.append(UNVERIFIED_NOTE)
    else:
        note_bits.append("no matching national-list entries")
    if unavailable:
        note_bits.append("unavailable: " + "; ".join(unavailable.values()))
    if errors:
        note_bits.append("errors: " + ", ".join(f"{k} ({v})" for k, v in errors.items()))
    if unknown:
        note_bits.append("unknown countries: " + ", ".join(unknown))

    return ConnectorResult(
        source="national_wanted", passive=True,
        raw={"query": name, "sources_used": used, "matches": simplified,
             "unavailable": unavailable, "errors": errors,
             "available_sources": {k: {"country": s.country, "verified": s.verified}
                                   for k, s in SOURCES.items()}},
        entities=entities, relationships=rels,
        note=" | ".join(note_bits),
    )
