"""Offline unit tests for connectors, with HTTP mocked via respx.

These never hit the network, so they're safe for CI. A separate live smoke
test (scripts/smoke.py) exercises the real sources.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from connectors.base import EntityType
from connectors.crtsh import crtsh_subdomains
from connectors.dork_search import dork_search
from connectors.gravatar import gravatar, gravatar_hash
from connectors.github_user import github_user
from connectors.rdap import rdap_domain


def _keys(result, etype: EntityType) -> set[str]:
    return {e.key for e in result.entities if e.type == etype}


@respx.mock
async def test_crtsh_extracts_subdomains():
    respx.get("https://crt.sh/").mock(return_value=httpx.Response(200, json=[
        {"name_value": "example.com\nwww.example.com", "serial_number": "AA",
         "issuer_name": "CA", "not_before": "2024-01-01", "not_after": "2025-01-01",
         "common_name": "example.com"},
        {"name_value": "*.dev.example.com", "serial_number": "BB"},
    ]))
    result = await crtsh_subdomains("example.com")
    subs = _keys(result, EntityType.DOMAIN)
    assert "www.example.com" in subs
    assert "dev.example.com" in subs
    assert "example.com" in subs  # the apex is present but not counted as a subdomain
    assert any(e.type == EntityType.CERTIFICATE for e in result.entities)
    assert result.passive is True


@respx.mock
async def test_crtsh_empty():
    respx.get("https://crt.sh/").mock(return_value=httpx.Response(200, json=[]))
    result = await crtsh_subdomains("nothing.example")
    assert result.note and "no certificates" in result.note


@respx.mock
async def test_rdap_parses_registrar_and_registrant():
    rdap = {
        "events": [
            {"eventAction": "registration", "eventDate": "2010-01-01T00:00:00Z"},
            {"eventAction": "expiration", "eventDate": "2030-01-01T00:00:00Z"},
        ],
        "nameservers": [{"ldhName": "NS1.EXAMPLE.COM"}],
        "status": ["client transfer prohibited"],
        "entities": [
            {"roles": ["registrar"],
             "vcardArray": ["vcard", [["fn", {}, "text", "Example Registrar Inc."]]]},
            {"roles": ["registrant"],
             "vcardArray": ["vcard", [["fn", {}, "text", "Example SRL"],
                                      ["email", {}, "text", "admin@example.com"]]]},
        ],
    }
    respx.get("https://rdap.org/domain/example.com").mock(
        return_value=httpx.Response(200, json=rdap))
    result = await rdap_domain("example.com")
    domain_entity = next(e for e in result.entities if e.type == EntityType.DOMAIN)
    assert domain_entity.attributes["registrar"] == "Example Registrar Inc."
    assert "ns1.example.com" in domain_entity.attributes["nameservers"]
    assert "example srl" in _keys(result, EntityType.ORGANIZATION)
    assert "admin@example.com" in _keys(result, EntityType.EMAIL)


@respx.mock
async def test_rdap_404():
    respx.get("https://rdap.org/domain/nope.example").mock(
        return_value=httpx.Response(404))
    result = await rdap_domain("nope.example")
    assert result.note and "no RDAP" in result.note


def test_gravatar_hash_is_trimmed_lowercased():
    assert gravatar_hash("  Foo@Example.com ") == gravatar_hash("foo@example.com")


@respx.mock
async def test_gravatar_not_found():
    email = "nobody@example.com"
    respx.get(f"https://www.gravatar.com/{gravatar_hash(email)}.json").mock(
        return_value=httpx.Response(404))
    result = await gravatar(email)
    assert result.note == "no public Gravatar profile"


@respx.mock
async def test_github_user_profile_and_email():
    respx.get("https://api.github.com/users/octocat").mock(return_value=httpx.Response(
        200, json={"login": "octocat", "html_url": "https://github.com/octocat",
                   "name": "The Octocat", "email": "octo@github.com",
                   "followers": 1000, "public_repos": 8}))
    respx.get("https://api.github.com/users/octocat/repos").mock(return_value=httpx.Response(
        200, json=[{"full_name": "octocat/hello", "html_url": "u", "language": "Ruby",
                    "stargazers_count": 1, "fork": False}]))
    result = await github_user("octocat")
    assert "octo@github.com" in _keys(result, EntityType.EMAIL)
    assert "github:octocat/hello" in _keys(result, EntityType.REPOSITORY)


@respx.mock
async def test_dork_search_duckduckgo_parses_and_unwraps():
    html = """
    <table>
      <tr><td><a class="result-link"
        href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fjane">Jane</a></td></tr>
      <tr><td class="result-snippet">About Jane</td></tr>
    </table>
    """
    respx.post("https://lite.duckduckgo.com/lite/").mock(
        return_value=httpx.Response(200, text=html))
    result = await dork_search('"Jane Doe"')
    urls = _keys(result, EntityType.URL)
    assert "https://example.com/jane" in urls
    assert result.passive is True


@respx.mock
async def test_dork_search_reports_throttle_as_error_not_empty():
    # A 200 with no parseable results must surface as an error, not a silent 0.
    respx.post("https://lite.duckduckgo.com/lite/").mock(
        return_value=httpx.Response(200, text="<html>no results table</html>"))
    result = await dork_search("whatever")
    assert result.entities == []
    assert result.raw["errors"].get("duckduckgo")
    assert result.note and "duckduckgo" in result.note
