"""Offline tests for the legal / law-enforcement connectors (HTTP mocked).

The important invariant: hits are low-confidence, unverified candidates with an
official-listing URL for evidence — never confirmations.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from connectors.base import EntityType
from connectors._legal import NAME_MATCH_QUALITY
from connectors.europol import europol_wanted, _parse_fugitives
from connectors.interpol import interpol_notices, _split_name
from connectors.sanctions import sanctions

EUMW_HTML = """
<div class="views-row">
  <div class="views-field views-field-nid"><span class="field-content">2117</span></div>
  <div class="views-field views-field-title"><span class="field-content">STOJILJKOVIC, Aleksandar</span></div>
  <div class="views-field views-field-view-node"><span class="field-content">/stojiljkovic-aleksandar</span></div>
  <div class="views-field views-field-field-enfast-country"><div class="field-content">Netherlands</div></div>
  <div class="views-field views-field-field-country-code"><div class="field-content">NL</div></div>
  <div class="views-field views-field-field-propable-locations"><div class="field-content">Serbia</div></div>
  <div class="views-field views-field-field-reward"><div class="field-content">-</div></div>
</div>
<div class="views-row">
  <div class="views-field views-field-title"><span class="field-content">DOE, John</span></div>
  <div class="views-field views-field-view-node"><span class="field-content">/doe-john</span></div>
  <div class="views-field views-field-field-enfast-country"><div class="field-content">France</div></div>
</div>
"""


def test_eumw_parser_extracts_structured_fields():
    rows = _parse_fugitives(EUMW_HTML)
    assert len(rows) == 2
    r = rows[0]
    assert r["name"] == "STOJILJKOVIC, Aleksandar"
    assert r["detail_url"] == "https://eumostwanted.eu/stojiljkovic-aleksandar"
    assert r["requesting_country"] == "Netherlands"
    assert r["probable_locations"] == "Serbia"


@respx.mock
async def test_europol_match_is_unverified_candidate_with_evidence_url():
    respx.get("https://eumostwanted.eu/").mock(return_value=httpx.Response(200, text=EUMW_HTML))
    result = await europol_wanted("stojiljkovic")

    persons = [e for e in result.entities if e.type == EntityType.PERSON]
    assert len(persons) == 1
    p = persons[0]
    assert p.match_quality == NAME_MATCH_QUALITY
    assert p.attributes["unverified"] is True
    assert p.attributes["list"] == "EU Most Wanted (ENFAST)"
    # evidence URL present for the report agent to screenshot
    urls = [e for e in result.entities if e.type == EntityType.URL]
    assert any("stojiljkovic" in u.key for u in urls)
    # and a relationship marked unverified
    assert result.relationships[0].type == "appears_in_wanted_list"
    assert result.relationships[0].attributes["unverified"] is True


@respx.mock
async def test_europol_no_match_notes_total():
    respx.get("https://eumostwanted.eu/").mock(return_value=httpx.Response(200, text=EUMW_HTML))
    result = await europol_wanted("nonexistent person")
    assert not [e for e in result.entities if e.type == EntityType.PERSON]
    assert "no match" in result.note and "2 listed" in result.note


def test_interpol_name_split():
    assert _split_name("STOJILJKOVIC, Aleksandar") == {"name": "STOJILJKOVIC", "forename": "Aleksandar"}
    assert _split_name("Vladimir Putin") == {"forename": "Vladimir", "name": "Putin"}
    assert _split_name("Madonna") == {"name": "Madonna"}


@respx.mock
async def test_interpol_parses_notices():
    body = {"total": 1, "_embedded": {"notices": [{
        "entity_id": "2020/12345", "forename": "JOHN", "name": "DOE",
        "date_of_birth": "1980/01/01", "nationalities": ["GB"],
        "_links": {"self": {"href": "https://ws-public.interpol.int/notices/v1/red/2020-12345"}},
    }]}}
    respx.get(url__startswith="https://ws-public.interpol.int/notices/v1/red").mock(
        return_value=httpx.Response(200, json=body))
    result = await interpol_notices("John Doe")
    persons = [e for e in result.entities if e.type == EntityType.PERSON]
    assert persons and persons[0].match_quality == NAME_MATCH_QUALITY
    assert persons[0].attributes["disambiguators"]["date_of_birth"] == "1980/01/01"
    assert persons[0].attributes["disambiguators"]["nationalities"] == ["GB"]


@respx.mock
async def test_interpol_403_degrades_gracefully():
    respx.get(url__startswith="https://ws-public.interpol.int/notices/v1/red").mock(
        return_value=httpx.Response(403, text="Access Denied"))
    result = await interpol_notices("whoever")
    assert result.entities == []
    assert "blocked this host" in result.note


async def test_sanctions_skips_without_key(monkeypatch):
    monkeypatch.delenv("OPENSANCTIONS_API_KEY", raising=False)
    monkeypatch.delenv("OPENSANCTIONS_API_URL", raising=False)
    result = await sanctions("vladimir putin")
    assert result.entities == []
    assert "OPENSANCTIONS_API_KEY not set" in result.note


@respx.mock
async def test_sanctions_parses_results_with_key(monkeypatch):
    monkeypatch.setenv("OPENSANCTIONS_API_KEY", "test-key")
    body = {"total": {"value": 1}, "results": [{
        "id": "Q7747", "caption": "Vladimir Putin", "schema": "Person",
        "datasets": ["eu_fsf", "us_ofac_sdn"],
        "properties": {"topics": ["sanction", "role.pep"], "country": ["ru"],
                       "birthDate": ["1952-10-07"]},
    }]}
    respx.get(url__startswith="https://api.opensanctions.org/search/default").mock(
        return_value=httpx.Response(200, json=body))
    result = await sanctions("vladimir putin")
    persons = [e for e in result.entities if e.type == EntityType.PERSON]
    assert persons[0].key == "vladimir putin"
    assert "sanction" in persons[0].attributes["topics"]
    assert persons[0].attributes["unverified"] is True
    urls = [e for e in result.entities if e.type == EntityType.URL]
    assert any("opensanctions.org/entities/Q7747" in u.key for u in urls)
