"""Offline tests for national_wanted (HTTP mocked)."""

from __future__ import annotations

import httpx
import pytest
import respx

from connectors.base import EntityType
from connectors._legal import NAME_MATCH_QUALITY
from connectors.national_wanted import national_wanted, _ro_parse_page, RO_LIST

FBI_BODY = {"total": 1, "items": [{
    "uid": "abc123", "title": "JOHN SILVA",
    "url": "https://www.fbi.gov/wanted/topten/john-silva",
    "subjects": ["Ten Most Wanted Fugitives"],
    "nationality": "American", "dates_of_birth_used": ["January 1, 1980"],
    "place_of_birth": "Texas", "aliases": ["Johnny"],
    "reward_text": "up to $100,000",
}]}


@respx.mock
async def test_fbi_hit_is_unverified_candidate_with_evidence():
    respx.get(url__startswith="https://api.fbi.gov/wanted/v1/list").mock(
        return_value=httpx.Response(200, json=FBI_BODY))
    result = await national_wanted("silva", countries=["fbi"])

    persons = [e for e in result.entities if e.type == EntityType.PERSON]
    assert len(persons) == 1
    p = persons[0]
    assert p.key == "john silva"
    assert p.match_quality == NAME_MATCH_QUALITY
    assert p.attributes["unverified"] is True
    assert p.attributes["requesting_country"] == "United States"
    assert p.attributes["disambiguators"]["nationality"] == "American"
    urls = [e for e in result.entities if e.type == EntityType.URL]
    assert any("fbi.gov/wanted/topten/john-silva" in u.key for u in urls)


@respx.mock
async def test_fbi_no_match():
    respx.get(url__startswith="https://api.fbi.gov/wanted/v1/list").mock(
        return_value=httpx.Response(200, json={"total": 0, "items": []}))
    result = await national_wanted("nobodyxyz", countries=["fbi"])
    assert not [e for e in result.entities if e.type == EntityType.PERSON]
    assert "no matching national-list entries" in result.note


async def test_blocked_eu_sources_degrade_with_note():
    # be/nl raise SourceUnavailable; must not fail, must be reported.
    # (ro is live/verified and makes a real request, so it's excluded here.)
    result = await national_wanted("whoever", countries=["be", "nl"])
    assert result.entities == []
    assert "unavailable" in result.note
    assert result.raw["unavailable"].keys() == {"be", "nl"}


async def test_unknown_country_reported():
    result = await national_wanted("x", countries=["atlantis"])
    assert "unknown countries: atlantis" in result.note


RO_PAGE1 = """
<a href="/ro/persoane-urmarite">Persoane urmarite</a>
<a href="/ro/persoane-urmarite/ion-popescu-1011980">ION POPESCU</a>
<a href="/ro/persoane-urmarite/maria-ionescu-2021990">MARIA IONESCU</a>
<a href="/ro/persoane-urmarite&page=2">Pagina următoare</a>
"""
RO_PAGE2 = """
<a href="/ro/persoane-urmarite/ion-vlad-3031975">ION VLAD</a>
"""


def test_ro_parser_extracts_persons_dob_and_next():
    persons, nxt = _ro_parse_page(RO_PAGE1)
    names = {p["name"] for p in persons}
    assert names == {"ION POPESCU", "MARIA IONESCU"}  # self + next link excluded
    popescu = next(p for p in persons if p["name"] == "ION POPESCU")
    assert popescu["dob_code"] == "1011980"
    assert popescu["url"].endswith("/ion-popescu-1011980")
    assert nxt and nxt.endswith("persoane-urmarite&page=2")


@respx.mock
async def test_romania_paginates_and_filters_by_name():
    respx.get(RO_LIST).mock(return_value=httpx.Response(200, text=RO_PAGE1))
    respx.get("https://www.politiaromana.ro/ro/persoane-urmarite&page=2").mock(
        return_value=httpx.Response(200, text=RO_PAGE2))
    # "vlad" only matches the person on PAGE 2 — proves pagination + filtering.
    result = await national_wanted("vlad", countries=["ro"])
    persons = [e for e in result.entities if e.type == EntityType.PERSON]
    keys = {p.key for p in persons}
    assert keys == {"ion vlad"}  # popescu/ionescu (page 1) filtered out
    p = persons[0]
    assert p.match_quality == NAME_MATCH_QUALITY
    assert p.attributes["unverified"] is True
    assert p.attributes["requesting_country"] == "Romania"
    assert p.attributes["disambiguators"]["dob_code"] == "3031975"


@respx.mock
async def test_one_source_blocked_other_works():
    respx.get(url__startswith="https://api.fbi.gov/wanted/v1/list").mock(
        return_value=httpx.Response(200, json=FBI_BODY))
    result = await national_wanted("silva", countries=["fbi", "be"])
    assert [e for e in result.entities if e.type == EntityType.PERSON]  # fbi hit
    assert "be" in result.raw["unavailable"]                            # be noted
