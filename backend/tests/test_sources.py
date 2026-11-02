"""Offline tests for the sources added from the Kali toolchain and the search /
reading stack (SearXNG, Lightpanda/Firecrawl/HTTP), plus quota and cost rules."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

import connectors
from app.scan import connectors_for
from connectors import ConnectorSpec, _cli, recon_tools
from connectors.base import ConnectorResult, EntityType
from connectors.dehashed import dehashed
from connectors.dork_search import dork_search
from connectors.email_finder import hunter, tomba
from connectors.otx import otx_passive_dns
from connectors.urlscan import urlscan
from connectors.web_read import web_read


def keys(result, etype):
    return {e.key for e in result.entities if e.type == etype}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("SEARXNG_URL", "FIRECRAWL_API_URL", "FIRECRAWL_API_KEY", "OSINT_KALI_SSH",
              "OSINT_LIGHTPANDA_CMD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("URLSCAN_API_KEY", "k")
    monkeypatch.setenv("OTX_API_KEY", "k")
    monkeypatch.setenv("HUNTER_API_KEY", "k")
    monkeypatch.setenv("TOMBA_API_KEY", "k")
    monkeypatch.setenv("TOMBA_API_SECRET", "s")
    monkeypatch.setenv("DEHASHED_API_KEY", "k")
    monkeypatch.setattr(_cli.shutil, "which", lambda _tool: None)


# --- search -------------------------------------------------------------------------


@respx.mock
async def test_searxng_is_the_default_engine_when_configured(monkeypatch):
    monkeypatch.setenv("SEARXNG_URL", "http://searxng:8080")
    respx.get("http://searxng:8080/search").mock(return_value=httpx.Response(200, json={
        "results": [{"url": "https://a.example/x", "title": "A", "content": "snip",
                     "engines": ["brave", "bing"]}]}))
    r = await dork_search('"Jane Doe"')
    assert r.raw["engines_used"] == ["searxng"]
    assert keys(r, EntityType.URL) == {"https://a.example/x"}
    assert r.entities[0].attributes["engine"] == "searxng:brave,bing"


@respx.mock
async def test_searxng_failure_falls_back_to_duckduckgo(monkeypatch):
    monkeypatch.setenv("SEARXNG_URL", "http://searxng:8080")
    respx.get("http://searxng:8080/search").mock(return_value=httpx.Response(500))
    respx.post("https://lite.duckduckgo.com/lite/").mock(return_value=httpx.Response(
        200, text='<a class="result-link" href="https://b.example/">B</a>'
                  '<td class="result-snippet">s</td>'))
    r = await dork_search("q")
    assert r.raw["engines_used"] == ["duckduckgo"] and "searxng" in r.raw["errors"]


# --- page reading -----------------------------------------------------------------------

PAGE = """<html><head><title>Example SRL</title>
<meta property="og:site_name" content="Example">
<script type="application/ld+json">{"@type":"Organization","name":"Example SRL",
 "sameAs":["https://www.linkedin.com/company/example-srl","https://x.com/examplesrl"]}</script>
</head><body><p>Contact: office@example.ro</p>
<a href="mailto:press@example.ro">press</a>
<a href="https://www.facebook.com/examplesrl">fb</a>
<a href="https://www.facebook.com/sharer.php?u=x">share</a>
<a href="https://example.ro/about">about</a>
<a href="https://partner.example.com/">partner</a></body></html>"""


@respx.mock
async def test_web_read_plain_http_extracts_contacts_and_profiles():
    respx.get("https://example.ro/contact").mock(return_value=httpx.Response(200, text=PAGE))
    r = await web_read("https://example.ro/contact")
    assert r.raw["backend"] == "http"
    assert keys(r, EntityType.EMAIL) == {"office@example.ro", "press@example.ro"}
    assert keys(r, EntityType.SOCIAL_PROFILE) == {
        "facebook:examplesrl", "linkedin:company/example-srl", "twitter:examplesrl"}
    assert "partner.example.com" in r.raw["external_domains"]
    assert r.raw["structured_data"]["meta"]["og:site_name"] == "Example"


@respx.mock
async def test_web_read_prefers_firecrawl_and_falls_back(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_URL", "http://firecrawl:3002")
    route = respx.post("http://firecrawl:3002/v2/scrape").mock(return_value=httpx.Response(
        200, json={"data": {"markdown": "# Hi office@example.ro", "html": PAGE,
                            "metadata": {"title": "Example SRL", "statusCode": 200,
                                         "url": "https://example.ro/contact"}}}))
    r = await web_read("https://example.ro/contact")
    assert route.called and r.raw["backend"] == "firecrawl"
    route.mock(return_value=httpx.Response(503))
    respx.get("https://example.ro/contact").mock(return_value=httpx.Response(200, text=PAGE))
    r = await web_read("https://example.ro/contact")
    assert r.raw["backend"] == "http" and "firecrawl" in r.raw["backend_errors"]


ARTICLE = ("<html><head><title>Board news</title><meta name='author' content='Jane Doe'>"
           "<meta property='article:published_time' content='2026-09-17'></head><body>"
           "<nav>Home | About | Blog</nav><article><h1>Board news</h1>"
           + "".join(f"<p>Paragraph {i}: the foundation's board met on day {i} and reviewed "
                     f"item {i * 7} of its agenda, covering grants, events and audits.</p>"
                     for i in range(1, 25))
           + "</article><footer>Contact board@example.org</footer></body></html>")
JS_SHELL = ("<html><head><title>App</title></head><body><noscript>You need to enable "
            "JavaScript to run this app.</noscript><div id='root'></div></body></html>")


@respx.mock
async def test_web_read_http_first_for_server_rendered_pages(monkeypatch):
    monkeypatch.setenv("OSINT_LIGHTPANDA_CMD", "lightpanda")
    calls = []

    async def fake_run(tool, args, timeout=0, env=None):
        calls.append(args)
        return 0, PAGE, ""

    monkeypatch.setattr(_cli, "run", fake_run)
    respx.get("https://example.org/news").mock(return_value=httpx.Response(200, text=ARTICLE))
    r = await web_read("https://example.org/news")
    assert r.raw["backend"] == "http" and not calls          # no browser needed
    assert r.raw["text_source"] == "main_content"
    assert "Home | About" not in r.raw["markdown"]          # boilerplate stripped
    assert r.raw["page_metadata"]["author"] == "Jane Doe"
    assert "board@example.org" in r.raw["emails"]            # footer contacts still found


@respx.mock
async def test_web_read_escalates_to_lightpanda_for_js_pages(monkeypatch):
    monkeypatch.setenv("OSINT_LIGHTPANDA_CMD", "lightpanda")
    calls = []

    async def fake_run(tool, args, timeout=0, env=None):
        calls.append(args[2])
        return 0, PAGE, ""

    monkeypatch.setattr(_cli, "run", fake_run)
    respx.get("https://example.ro/contact").mock(return_value=httpx.Response(200, text=JS_SHELL))
    r = await web_read("https://example.ro/contact")
    assert r.raw["backend"] == "lightpanda" and calls == ["html"]  # one render per page
    assert "office@example.ro" in r.raw["emails"]
    assert "http" in r.raw["backend_errors"]


# --- passive data sources -----------------------------------------------------------------


@respx.mock
async def test_urlscan_maps_scans_to_hosts_and_ips():
    respx.get("https://urlscan.io/api/v1/search/").mock(return_value=httpx.Response(200, json={
        "total": 2, "results": [
            {"task": {"time": "2026-01-01"}, "page": {"url": "https://shop.example.com/",
             "domain": "shop.example.com", "ip": "203.0.113.5", "asn": "AS1"}},
            {"task": {}, "page": {"domain": "cdn.other.net", "ip": "198.51.100.1"}}]}))
    r = await urlscan("example.com")
    assert keys(r, EntityType.DOMAIN) == {"example.com", "shop.example.com"}
    assert keys(r, EntityType.IP) == {"203.0.113.5", "198.51.100.1"}


@respx.mock
async def test_otx_keeps_only_hosts_under_the_domain():
    respx.get("https://otx.alienvault.com/api/v1/indicators/domain/example.com/passive_dns"
              ).mock(return_value=httpx.Response(200, json={"count": 3, "passive_dns": [
                  {"hostname": "old.example.com", "address": "192.0.2.1"},
                  {"hostname": "example.com", "address": "lb.cdn.net"},
                  {"hostname": "evil.net", "address": "192.0.2.9"}]}))
    r = await otx_passive_dns("example.com")
    assert keys(r, EntityType.DOMAIN) == {"example.com", "old.example.com"}
    assert keys(r, EntityType.IP) == {"192.0.2.1"}


@respx.mock
async def test_email_finders_link_people_to_the_domain_not_a_guessed_org():
    respx.get("https://api.hunter.io/v2/domain-search").mock(return_value=httpx.Response(
        200, json={"data": {"organization": "Phyton", "pattern": "{first}", "emails": [
            {"value": "jane@example.com", "type": "personal", "confidence": 90,
             "first_name": "Jane", "last_name": "Doe", "position": "CTO",
             "linkedin": "https://www.linkedin.com/in/janedoe"},
            {"value": "info@example.com", "type": "generic", "confidence": 70}]}}))
    r = await hunter("example.com")
    assert keys(r, EntityType.EMAIL) == {"jane@example.com", "info@example.com"}
    assert keys(r, EntityType.PERSON) == {"jane doe"}
    assert not keys(r, EntityType.ORGANIZATION)
    dom = next(e for e in r.entities if e.type == EntityType.DOMAIN)
    assert dom.attributes["hunter_org_label"] == "Phyton"
    assert {x.type for x in r.relationships} >= {"affiliated_with", "uses_email", "has_profile"}

    respx.get("https://api.tomba.io/v1/domain-search").mock(return_value=httpx.Response(
        200, json={"data": {"organization": {"organization": "X"}, "emails": [
            {"email": "bob@example.com", "type": "personal", "score": 80,
             "first_name": "Bob", "last_name": "Ray"}]}}))
    t = await tomba("example.com")
    assert keys(t, EntityType.PERSON) == {"bob ray"}


@respx.mock
async def test_dehashed_extracts_pii_from_breach_records():
    respx.post("https://api.dehashed.com/v2/search").mock(return_value=httpx.Response(
        200, json={"total": 2, "balance": 97, "entries": [
            {"database_name": "ExampleLeak 2021", "email": ["jane@example.com"],
             "password": ["hunter2"], "phone": ["+40700"], "address": ["Str. X 1"],
             "name": ["Jane Doe"]},
            {"database_name": "ExampleLeak 2021", "email": ["jane@example.com"],
             "hashed_password": ["$2y$10$abc"]}]}))
    r = await dehashed("jane@example.com")
    blob = json.dumps(r.to_dict())
    # PII values are now kept in the graph
    assert "hunter2" in blob
    assert "$2y$10$abc" in blob
    assert "+40700" in blob
    assert "str. x 1" in blob
    assert "jane doe" in blob
    breach = next(e for e in r.entities if e.type == EntityType.BREACH)
    assert breach.attributes["data_classes"] == ["address", "credential", "email", "name", "phone"]
    breach_rel = next(rel for rel in r.relationships if rel.type == "appears_in_breach")
    assert breach_rel.attributes["credential_exposed"] is True
    assert "hunter2" in breach_rel.attributes["passwords"]
    assert "$2y$10$abc" in breach_rel.attributes["hashed_passwords"]
    phone = next(e for e in r.entities if e.type == EntityType.PHONE)
    assert phone.key == "+40700"
    loc = next(e for e in r.entities if e.type == EntityType.LOCATION)
    assert loc.key == "str. x 1"
    person = next(e for e in r.entities if e.type == EntityType.PERSON)
    assert person.key == "jane doe"


# --- command-line tools ------------------------------------------------------------------


async def test_cli_tools_parse_output(monkeypatch):
    outputs = {
        "subfinder": ('{"host":"api.example.com","source":"crtsh"}\n'
                      '{"host":"api.example.com","source":"alienvault"}\n'
                      '{"host":"unrelated.org","source":"x"}\n'),
        "dnstwist": json.dumps([{"fuzzer": "*original", "domain": "example.com"},
                                {"fuzzer": "homoglyph", "domain": "examp1e.com",
                                 "dns_a": ["192.0.2.1"], "dns_mx": ["mx.examp1e.com"]}]),
        "holehe": "[+] Email used, [-] Email not used\n[+] instagram.com\n[-] x.com\n",
        "maigret": ("[+] GitHub: https://github.com/jdoe\n"
                    "[+] OP.GG: https://op.gg/search?q=jdoe\n[+] Discord: https://discord.com\n"),
    }

    async def fake_run(tool, args, timeout=0, env=None):
        return 0, outputs[tool], ""

    monkeypatch.setattr(_cli, "run", fake_run)
    monkeypatch.setattr(_cli, "resolve", lambda tool: [tool])
    sub = await recon_tools.subfinder("example.com")
    assert keys(sub, EntityType.DOMAIN) == {"example.com", "api.example.com"}
    assert sub.raw["subdomains"]["api.example.com"] == ["alienvault", "crtsh"]
    tw = await recon_tools.dnstwist("example.com")
    assert tw.raw["with_mx"] == 1 and keys(tw, EntityType.DOMAIN) == {"example.com", "examp1e.com"}
    ho = await recon_tools.holehe("jane@example.com")
    assert ho.raw["registered_on"] == ["instagram.com"]
    mg = await recon_tools.maigret("jdoe")
    assert mg.raw["found"] == {"GitHub": "https://github.com/jdoe"}


def test_cli_resolution_order(monkeypatch):
    monkeypatch.setenv("OSINT_KALI_SSH", "kali")
    assert _cli.resolve("holehe")[:1] == ["ssh"] and _cli.resolve("holehe")[-1] == "holehe"
    monkeypatch.setattr(_cli.shutil, "which", lambda t: f"/usr/bin/{t}")
    assert _cli.resolve("holehe") == ["/usr/bin/holehe"]
    monkeypatch.setenv("OSINT_HOLEHE_CMD", "docker run --rm holehe")
    assert _cli.resolve("holehe") == ["docker", "run", "--rm", "holehe"]
    with pytest.raises(ValueError):
        _ = [recon_tools._domain("not a domain; rm -rf /")]


# --- quota, cost tiers, seed-only -----------------------------------------------------------


async def _metered(domain: str) -> ConnectorResult:
    return ConnectorResult(source="hunter", passive=True, raw={})


async def test_ledger_enforces_quota(platform, make_investigation, fake_registry):
    fake_registry["hunter"] = ConnectorSpec("hunter", _metered, EntityType.DOMAIN, True, "m",
                                            cost="metered", quota=2, quota_days=30)
    inv = make_investigation()
    for _ in range(2):
        out = await platform.ledger.run(inv, "hunter", "example.com", user="t", run_by="t")
        assert not out.refused
    out = await platform.ledger.run(inv, "hunter", "example.com", user="t", run_by="t")
    assert out.refused and "quota used up" in out.error


def test_scan_cost_tiers_and_seed_only(fake_registry, monkeypatch):
    fake_registry["hunter"] = ConnectorSpec("hunter", _metered, EntityType.DOMAIN, True, "m",
                                            cost="metered", quota=50, seed_only=True)
    ent = {"type": "Domain", "key": "example.com"}
    inv = {"mode": "passive", "settings": {}}
    assert "hunter" in connectors_for(ent, inv)              # default: every tier
    inv["settings"] = {"costly_connectors": "none"}
    assert "hunter" not in connectors_for(ent, inv)          # free sources only
    inv["settings"] = {"costly_connectors": "metered"}
    assert "hunter" in connectors_for(ent, inv, depth=0)
    assert "hunter" not in connectors_for(ent, inv, depth=1)  # seed-only


def test_registry_declares_costs():
    reg = connectors.REGISTRY
    assert reg["dehashed"].cost == "paid" and reg["hunter"].cost == "metered"
    assert reg["hunter"].quota == 50 and reg["tomba"].quota == 25
    assert all(reg[n].seed_only for n in ("holehe", "maigret", "dehashed", "dnstwist"))
