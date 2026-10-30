"""Offline tests for company registries, web archive history and HIBP."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.detect import detect
from app.scan import connectors_for
from connectors.archive_breach import hibp, wayback
from connectors.base import EntityType
from connectors.registries import anaf, companies_house, gleif, vies
from connectors.web_read import web_read

ORG = EntityType.ORGANIZATION


def keys(r, etype):
    return {e.key for e in r.entities if e.type == etype}


ANAF_FOUND = {"found": [{
    "date_generale": {"cui": 14399840, "denumire": "DANTE INTERNATIONAL SA",
                      "adresa": "MUNICIPIUL BUCURESTI, SECTOR 2, STR. GARA HERASTRAU, NR.6",
                      "nrRegCom": "J2002000372404", "cod_CAEN": "4754",
                      "data_inregistrare": "2002-01-23", "stare_inregistrare": "INREGISTRAT",
                      "forma_juridica": "SOCIETATE PE ACTIUNI", "telefon": ""},
    "inregistrare_scop_Tva": {"scpTVA": True},
    "stare_inactiv": {"statusInactivi": False, "dataRadiere": ""},
    "adresa_sediu_social": {"sdenumire_Strada": "Sos. Virtutii", "snumar_Strada": "148",
                            "sdenumire_Localitate": "Sector 6", "sdenumire_Judet": "BUCURESTI"}}],
    "notFound": []}


@respx.mock
async def test_anaf_maps_company_record():
    route = respx.post("https://webservicesp.anaf.ro/api/PlatitorTvaRest/v9/tva").mock(
        return_value=httpx.Response(200, json=ANAF_FOUND))
    r = await anaf("RO 14399840")
    assert json.loads(route.calls[0].request.content)[0]["cui"] == 14399840
    org = r.entities[0]
    assert org.key == "RO14399840" and org.attributes["registration_number"] == "J2002000372404"
    assert org.attributes["vat_registered"] is True and "phone" not in org.attributes
    assert len(keys(r, EntityType.LOCATION)) == 2 and r.note is None


async def test_anaf_skips_non_cui():
    r = await anaf("example srl")
    assert "CUI" in r.note and not r.entities


@respx.mock
async def test_anaf_flags_inactive():
    inactive = json.loads(json.dumps(ANAF_FOUND))
    inactive["found"][0]["stare_inactiv"]["statusInactivi"] = True
    respx.post("https://webservicesp.anaf.ro/api/PlatitorTvaRest/v9/tva").mock(
        return_value=httpx.Response(200, json=inactive))
    r = await anaf("14399840")
    assert "inactive" in r.note


@respx.mock
async def test_vies_valid_and_invalid():
    respx.get("https://ec.europa.eu/taxation_customs/vies/rest-api/ms/RO/vat/14399840").mock(
        return_value=httpx.Response(200, json={"isValid": True, "name": "DANTE INTERNATIONAL SA",
                                               "address": "BUCURESTI STR. GARA 6"}))
    r = await vies("14399840")
    assert r.entities[0].key == "RO14399840" and r.entities[0].attributes["vat_valid"] is True
    respx.get("https://ec.europa.eu/taxation_customs/vies/rest-api/ms/DE/vat/123").mock(
        return_value=httpx.Response(200, json={"isValid": False, "userError": "INVALID",
                                               "name": "---", "address": "---"}))
    bad = await vies("DE123")
    assert "not a valid VAT" in bad.note and "legal_name" not in bad.entities[0].attributes


GLEIF_REC = {"data": [{"attributes": {"lei": "315700V12MDD9PTKU295",
    "registration": {"status": "ISSUED"},
    "entity": {"legalName": {"name": "Dante International SA"}, "status": "ACTIVE",
               "jurisdiction": "RO", "registeredAs": "14399840",
               "legalAddress": {"addressLines": ["Virtutii 148"], "city": "Bucuresti",
                                "country": "RO"},
               "headquartersAddress": {"addressLines": ["Virtutii 148"], "city": "Bucuresti",
                                       "country": "RO"}}}}]}


@respx.mock
async def test_gleif_links_lei_to_national_registry_and_parent():
    respx.get("https://api.gleif.org/api/v1/lei-records").mock(
        return_value=httpx.Response(200, json=GLEIF_REC))
    respx.get("https://api.gleif.org/api/v1/lei-records/315700V12MDD9PTKU295/ultimate-parent").mock(
        return_value=httpx.Response(200, json={"data": {"attributes": {
            "lei": "21380044BQ8812EEKT59", "entity": {"legalName": {"name": "Naspers"},
                                                      "jurisdiction": "ZA"}}}}))
    r = await gleif("Dante International SA")
    assert {"dante international sa", "RO14399840", "naspers"} <= keys(r, ORG)
    types = {(x.type, x.to_key) for x in r.relationships}
    assert ("same_as", "RO14399840") in types and ("owned_by", "naspers") in types
    assert len(keys(r, EntityType.LOCATION)) == 1  # identical legal/HQ address kept once


@respx.mock
async def test_companies_house_officers_and_pscs(monkeypatch):
    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "k")
    base = "https://api.company-information.service.gov.uk/company/00445790"
    respx.get(base).mock(return_value=httpx.Response(200, json={
        "company_name": "TESCO PLC", "company_status": "active", "date_of_creation": "1947-11-27",
        "registered_office_address": {"address_line_1": "Tesco House", "locality": "Welwyn",
                                      "postal_code": "AL7 1GA"}}))
    respx.get(base + "/officers").mock(return_value=httpx.Response(200, json={"items": [
        {"name": "MURPHY, Ken", "officer_role": "director", "appointed_on": "2020-10-01"},
        {"name": "ACME SECRETARIES LTD", "officer_role": "corporate-secretary"}]}))
    respx.get(base + "/persons-with-significant-control").mock(
        return_value=httpx.Response(404))
    r = await companies_house("GB00445790")
    assert "GB00445790" in keys(r, ORG) and "acme secretaries ltd" in keys(r, ORG)
    assert keys(r, EntityType.PERSON) == {"ken murphy"}
    officer = next(x for x in r.relationships if x.from_key == "ken murphy")
    assert officer.type == "officer_of" and officer.attributes["appointed"] == "2020-10-01"


async def test_companies_house_needs_key(monkeypatch):
    monkeypatch.delenv("COMPANIES_HOUSE_API_KEY", raising=False)
    assert "not set" in (await companies_house("GB00445790")).note


@respx.mock
async def test_wayback_summarises_history():
    respx.get("https://web.archive.org/cdx/search/cdx").mock(return_value=httpx.Response(
        200, json=[["timestamp", "original", "statuscode", "mimetype"],
                   ["20090101000000", "http://example.com/", "200", "text/html"],
                   ["20120101000000", "http://old.example.com/admin/", "200", "text/html"],
                   ["20150101000000", "http://example.com/files/report.pdf", "200", "application/pdf"],
                   ["20200101000000", "http://cdn.other.net/x.js", "200", "text/javascript"]]))
    r = await wayback("example.com")
    assert r.raw["first_capture"].startswith("2009") and r.raw["captures"] == 4
    assert "old.example.com" in keys(r, EntityType.DOMAIN)
    assert "cdn.other.net" not in keys(r, EntityType.DOMAIN)
    urls = {i["url"] for i in r.raw["interesting"]}
    assert urls == {"http://old.example.com/admin/", "http://example.com/files/report.pdf"}


@respx.mock
async def test_hibp_found_and_not_found(monkeypatch):
    monkeypatch.setenv("HIBP_API_KEY", "k")
    respx.get("https://haveibeenpwned.com/api/v3/breachedaccount/jane@example.com").mock(
        return_value=httpx.Response(200, json=[{"Name": "Adobe", "Title": "Adobe",
                                                "BreachDate": "2013-10-04",
                                                "DataClasses": ["Email addresses", "Passwords"]}]))
    r = await hibp("Jane@Example.com")
    assert keys(r, EntityType.BREACH) == {"adobe"}
    assert r.relationships[0].attributes["credential_exposed"] is True
    respx.get("https://haveibeenpwned.com/api/v3/breachedaccount/clean@example.com").mock(
        return_value=httpx.Response(404))
    assert "not found" in (await hibp("clean@example.com")).note


@respx.mock
async def test_web_read_finds_company_identifiers():
    page = ("<html><body><article>" + "".join(f"<p>Product line {i} with details.</p>"
                                               for i in range(40)) +
            "</article><footer>Dante International SA, CUI: RO14399840, J40/372/2002. "
            "Example Ltd, registered in England and Wales, company number 00445790."
            "</footer></body></html>")
    respx.get("https://shop.example.ro/").mock(return_value=httpx.Response(200, text=page))
    r = await web_read("https://shop.example.ro/")
    assert r.raw["company_identifiers"] == ["GB00445790", "RO14399840"]
    assert {"RO14399840", "GB00445790"} <= keys(r, ORG)


@pytest.mark.parametrize("value,key", [("RO14399840", "RO14399840"), ("14399840", "RO14399840"),
                                       ("GB00445790", "GB00445790"), ("uk SC123456", "GBSC123456")])
def test_detect_company_identifiers(value, key):
    assert detect(value) == (ORG, key)


def test_scan_only_runs_matching_registries():
    inv = {"mode": "passive", "settings": {}}
    ro = connectors_for({"type": "Organization", "key": "RO14399840", "label": "x",
                         "attributes": {}}, inv)
    gb = connectors_for({"type": "Organization", "key": "GB00445790", "label": "x",
                         "attributes": {}}, inv)
    named = connectors_for({"type": "Organization", "key": "example srl", "label": "Example",
                            "attributes": {}}, inv)
    assert {"anaf", "vies"} <= set(ro) and "companies_house" not in ro
    assert "anaf" not in gb and "anaf" not in named and "vies" not in named
    assert "gleif" in named
