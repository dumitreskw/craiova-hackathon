"""Shared fixtures for the app tests: a temp-dir platform and fake connectors.

The fakes replace ``connectors.REGISTRY`` so scans, the ledger and the API run
end to end with no network.
"""

from __future__ import annotations

import pytest

import connectors
from app.config import Settings
from app.platform import Platform
from connectors import ConnectorSpec
from connectors._legal import legal_hit
from connectors.base import ConnectorResult, EntityCandidate, EntityType, RelationshipCandidate

D, IP, EMAIL, URL, PERSON, USER = (EntityType.DOMAIN, EntityType.IP, EntityType.EMAIL,
                                   EntityType.URL, EntityType.PERSON, EntityType.USERNAME)


async def fake_dns(domain: str) -> ConnectorResult:
    return ConnectorResult(
        source="dns", passive=True, raw={"domain": domain, "A": ["203.0.113.10"]},
        entities=[EntityCandidate(D, domain), EntityCandidate(IP, "203.0.113.10"),
                  EntityCandidate(EMAIL, f"hostmaster@{domain}", match_quality=0.7),
                  EntityCandidate(D, "ns1.dnsprovider.net"),
                  EntityCandidate(EMAIL, "noc@dnsprovider.net", match_quality=0.7)],
        relationships=[
            RelationshipCandidate("resolves_to", D, domain, IP, "203.0.113.10"),
            RelationshipCandidate("uses_email", D, domain, EMAIL, f"hostmaster@{domain}"),
            RelationshipCandidate("nameserver", D, domain, D, "ns1.dnsprovider.net"),
            RelationshipCandidate("uses_email", D, domain, EMAIL, "noc@dnsprovider.net")])


async def fake_rdap(domain: str) -> ConnectorResult:
    return ConnectorResult(
        source="rdap", passive=True, raw={"registrar": "Example Registrar"},
        entities=[EntityCandidate(D, domain, {"registrar": "Example Registrar"}),
                  EntityCandidate(EntityType.ORGANIZATION, "example srl")],
        relationships=[RelationshipCandidate("registered_by", D, domain,
                                             EntityType.ORGANIZATION, "example srl")])


async def fake_crtsh(domain: str) -> ConnectorResult:
    subs = [f"www.{domain}", f"dev.{domain}"]
    return ConnectorResult(
        source="crtsh", passive=True, raw={"subdomains": subs},
        entities=[EntityCandidate(D, domain)] + [EntityCandidate(D, s) for s in subs],
        relationships=[RelationshipCandidate("subdomain_of", D, s, D, domain) for s in subs])


async def fake_http_probe(domain: str) -> ConnectorResult:
    return ConnectorResult(source="http_probe", passive=False, raw={"title": "Example"},
                           entities=[EntityCandidate(URL, f"https://{domain}/")])


async def fake_gravatar(email: str) -> ConnectorResult:
    return ConnectorResult(
        source="gravatar", passive=True, raw={},
        entities=[EntityCandidate(EMAIL, email),
                  EntityCandidate(USER, "jdoe"),
                  EntityCandidate(PERSON, "jane doe")],
        relationships=[RelationshipCandidate("uses_username", EMAIL, email, USER, "jdoe"),
                       RelationshipCandidate("uses_email", PERSON, "jane doe", EMAIL, email)])


async def fake_github(username: str) -> ConnectorResult:
    return ConnectorResult(source="github_user", passive=True, raw={"login": username},
                           entities=[EntityCandidate(USER, username)])


async def fake_dork(query: str, engines=None) -> ConnectorResult:
    return ConnectorResult(
        source="dork_search", passive=True, raw={"query": query},
        entities=[EntityCandidate(URL, "https://news.example/article", {"title": "Article"},
                                  match_quality=0.5)])


async def fake_interpol(name: str, notice_type: str = "red") -> ConnectorResult:
    ents, rels = [], []
    for i, dob in enumerate(["1970-01-01", "1985-05-05"]):
        e, r = legal_hit(subject_name=name, subject_type=PERSON, edge="appears_in_wanted_list",
                         source_url=f"https://interpol.example/notice/{i}", source="interpol",
                         attributes={"list": "INTERPOL RED Notice", "title": name.title()},
                         disambiguators={"date_of_birth": dob})
        ents += e
        rels += r
    return ConnectorResult(source="interpol", passive=True, raw={"total": 2},
                           entities=ents, relationships=rels)


async def fake_europol(name: str) -> ConnectorResult:
    return ConnectorResult(source="europol", passive=True, raw={"total": 0})


async def fake_broken(domain: str) -> ConnectorResult:
    raise RuntimeError("upstream exploded")


FAKES = {
    "dns": (fake_dns, D, True), "rdap": (fake_rdap, D, True), "crtsh": (fake_crtsh, D, True),
    "http_probe": (fake_http_probe, D, False), "gravatar": (fake_gravatar, EMAIL, True),
    "github_user": (fake_github, USER, True), "dork_search": (fake_dork, PERSON, True),
    "interpol": (fake_interpol, PERSON, True), "europol": (fake_europol, PERSON, True),
}


@pytest.fixture
def fake_registry(monkeypatch):
    registry = {name: ConnectorSpec(name, fn, accepts, passive, f"fake {name}")
                for name, (fn, accepts, passive) in FAKES.items()}
    monkeypatch.setattr(connectors, "REGISTRY", registry)
    return registry


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, retention_days=90, max_calls_per_scan=60,
                    max_pivots_per_type=10)


@pytest.fixture
def platform(settings, fake_registry):
    p = Platform(settings)
    yield p
    p.close()


@pytest.fixture
def make_investigation(platform):
    from app.models import new_id, now

    def make(**over):
        inv = {"id": new_id("inv"), "name": "Test", "purpose": "Authorized test assessment",
               "legal_basis": "legitimate_interest", "mode": "passive", "scope": [],
               "authorization": None, "settings": {"max_depth": 1}, "retention_days": 90,
               "legal_hold": 0, "created_by": "tester", "created_at": now(), **over}
        platform.db.insert("investigations", inv)
        return platform.db.get("investigations", inv["id"])

    return make
