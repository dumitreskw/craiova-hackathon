"""Telling the subject apart from namesakes (app/identity.py): no name-only
merges, the subject profile, automatic rejection on clear conflicts, searching
with context, the who's-who, verdicts on the graph, and LinkedIn only through
research accounts."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app import identity, review
from app.agent.tools import ToolBox, verify_tools
from app.evidence import capture, read_page
from app.main import create_app
from app.models import new_id, now
from app.scan import PIVOTS, arg_for
from connectors import ConnectorSpec
from connectors.base import ConnectorResult, EntityCandidate, EntityType, RelationshipCandidate

PERSON, ORG, EMAIL = EntityType.PERSON, EntityType.ORGANIZATION, EntityType.EMAIL


async def fake_officers(company: str) -> ConnectorResult:
    """A registry that lists an officer with the subject's name (word order flipped)."""
    return ConnectorResult(
        source="companies_house", passive=True, raw={"company": company},
        entities=[EntityCandidate(ORG, company, {"legal_name": "Acme Holdings Ltd"}),
                  EntityCandidate(PERSON, "popescu ion", {
                      "title": "Ion Popescu", "nationality": "British",
                      "occupation": "Footballer"}, match_quality=0.9),
                  EntityCandidate(EMAIL, "ion@acme.example")],
        relationships=[RelationshipCandidate("officer_of", PERSON, "popescu ion", ORG, company),
                       RelationshipCandidate("uses_email", PERSON, "popescu ion",
                                             EMAIL, "ion@acme.example")])


async def fake_dehashed(email: str) -> ConnectorResult:
    return ConnectorResult(
        source="dehashed", passive=True, raw={},
        entities=[EntityCandidate(EMAIL, email), EntityCandidate(PERSON, "ión popescu",
                                                                 {"title": "Ión Popescu"})],
        relationships=[RelationshipCandidate("identified_as", EMAIL, email,
                                             PERSON, "ión popescu")])


@pytest.fixture
def extra(fake_registry):
    fake_registry["companies_house"] = ConnectorSpec("companies_house", fake_officers, ORG,
                                                     True, "fake")
    fake_registry["dehashed"] = ConnectorSpec("dehashed", fake_dehashed, EMAIL, True, "fake",
                                              cost="paid", seed_only=True)
    return fake_registry


def _target(p, inv, value, etype, key, profile=None):
    eid = p.resolver.ensure_entity(inv["id"], etype, key)
    p.db.insert("targets", {"id": new_id("t"), "investigation_id": inv["id"], "value": value,
                            "type": etype.value, "entity_id": eid, "added_by": "t",
                            "created_at": now(), "profile": profile or {}})
    return eid


def _persons(p):
    return p.db.query("SELECT * FROM entities WHERE type = 'Person' ORDER BY key")


# --- names --------------------------------------------------------------------------


def test_names_compare_without_diacritics_or_word_order():
    assert identity.same_name("Popescu Ion", "Ion Popescu")
    assert identity.same_name("Ștefan Țurcan", "turcan stefan")
    assert identity.same_name("ION  POPESCU", "ión popescu @ GB123")
    assert not identity.same_name("Ion Popescu", "Ioana Popescu")


# --- 1. no name-only merges -------------------------------------------------------------


async def test_same_name_officer_stays_a_separate_namesake(platform, make_investigation, extra):
    inv = make_investigation()
    person = _target(platform, inv, "Ion Popescu", PERSON, "ion popescu")
    await platform.ledger.run(inv, "companies_house", "GB00000001", user="t", run_by="t")
    target = platform.db.get("entities", person)
    assert target["attributes"] == {}            # no job, nationality or email borrowed
    other = next(r for r in _persons(platform) if r["id"] != person)
    assert other["key"] == "popescu ion @ GB00000001"
    assert other["attributes"]["namesake"][0]["value"] is True
    edge = platform.db.one("SELECT * FROM edges WHERE type = 'possible_match'")
    assert (edge["from_id"], edge["to_id"]) == (person, other["id"])
    # The namesake's email hangs off the namesake, not the subject.
    email = platform.db.one("SELECT id FROM entities WHERE key = 'ion@acme.example'")["id"]
    assert platform.db.one("SELECT * FROM edges WHERE from_id = ? AND to_id = ?",
                           (other["id"], email))
    assert not platform.db.one("SELECT * FROM edges WHERE from_id = ? AND to_id = ?",
                               (person, email))


async def test_officer_of_the_subjects_employer_merges(platform, make_investigation, extra):
    inv = make_investigation()
    person = _target(platform, inv, "Ion Popescu", PERSON, "ion popescu",
                     {"employers": ["Acme Holdings Ltd"]})
    await platform.ledger.run(inv, "companies_house", "GB00000001", user="t", run_by="t")
    assert [r["id"] for r in _persons(platform)] == [person]
    assert platform.db.get("entities", person)["attributes"]["occupation"][0]["value"] \
        == "Footballer"


async def test_name_from_the_subjects_own_email_merges(platform, make_investigation, extra):
    inv = make_investigation()
    person = _target(platform, inv, "Ion Popescu", PERSON, "ion popescu")
    email = _target(platform, inv, "ion@example.com", EMAIL, "ion@example.com")
    await platform.ledger.run(inv, "dehashed", "ion@example.com", user="t", run_by="t",
                              entity_id=email)
    assert [r["id"] for r in _persons(platform)] == [person]
    assert platform.db.one("SELECT * FROM edges WHERE type = 'identified_as' AND to_id = ?",
                           (person,))


# --- 3. automatic rejection on clear conflicts ------------------------------------------------


def test_profile_conflict_rules():
    profile = {"birth_year": 1985, "nationalities": ["Romanian"]}
    assert "1962" in identity.profile_conflict(profile, {"date_of_birth": "1962/03/04"})
    assert identity.profile_conflict(profile, {"date_of_birth": "1986-01-01"}) is None
    assert "RU" in identity.profile_conflict(profile, {"nationalities": ["RU"]})
    assert identity.profile_conflict(profile, {"countries": ["ro", "md"]}) is None
    assert identity.profile_conflict(profile, {}) is None
    assert identity.profile_conflict({}, {"date_of_birth": "1962"}) is None


async def test_conflicting_list_match_is_rejected_and_can_be_overridden(platform,
                                                                         make_investigation):
    inv = make_investigation()
    person = _target(platform, inv, "Jane Doe", PERSON, "jane doe", {"birth_year": 1985})
    await platform.ledger.run(inv, "interpol", "jane doe", user="t", run_by="t",
                              entity_id=person)
    hits = platform.db.query("SELECT * FROM entities WHERE type = 'Person' AND id != ? "
                             "ORDER BY key", (person,))
    by_status = {h["status"]: h for h in hits}
    assert set(by_status) == {"rejected", "candidate"}     # 1970 rejected, 1985 kept
    rv = review.latest(platform.db, by_status["rejected"]["id"])
    assert rv["reviewer"] == identity.PROFILE_CHECK and "1970" in rv["reason"]
    # The analyst can still override it; confirming stays a human decision.
    review.record(platform.db, platform.resolver, platform.bus, by_status["rejected"]["id"],
                  "confirmed", "same passport photo", reviewer="ana", user="ana",
                  by_agent=False)
    assert platform.db.get("entities", by_status["rejected"]["id"])["status"] == "confirmed"


# --- 4 & 8. the scan --------------------------------------------------------------------------


def test_person_search_uses_the_profile():
    ent = {"type": "Person", "key": "ion popescu", "label": "Ion Popescu"}
    assert arg_for("dork_search", ent) == '"Ion Popescu"'
    assert arg_for("dork_search", ent, {"locations": ["Craiova"]}) == '"Ion Popescu" "Craiova"'
    assert arg_for("dork_search", ent, {"employers": ["Acme"], "locations": ["Craiova"]}) \
        == '"Ion Popescu" "Acme"'


def test_quick_scan_never_spends_dehashed_credits():
    assert not any("dehashed" in names for names in PIVOTS.values())


async def test_scan_searches_the_person_with_context(platform, make_investigation):
    inv = make_investigation(settings={"max_depth": 0})
    person = _target(platform, inv, "Ion Popescu", PERSON, "ion popescu",
                     {"locations": ["Craiova"]})
    await platform.scan.run("ar_x", inv, [person], "t")
    f = platform.db.one("SELECT * FROM findings WHERE connector = 'dork_search'")
    assert f["input"]["arg"] == '"Ion Popescu" "Craiova"'


# --- 6. LinkedIn only through research accounts --------------------------------------------------


async def test_linkedin_is_refused_anonymously(platform, make_investigation):
    inv = make_investigation()
    for fn in (read_page, capture):
        out = await fn(platform.ledger, inv, "https://www.linkedin.com/in/jane-doe",
                       user="t", run_by="t")
        assert out.refused and "logged-in research account" in out.error
    box = ToolBox(platform.ledger, inv, "ar_x", "t", budget=5)
    msg, err = await box.call("read_page", {"url": "https://ro.linkedin.com/in/jane-doe"})
    assert err and "Linkedin pages show a login wall" in msg and box.calls == 0


# --- 2, 5 & 7. API: profile, identity, who's who, verdicts on the graph --------------------------


@pytest.fixture
async def client(platform):
    app = create_app(platform)
    app.state.platform = platform
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test/api/v1") as c:
        yield c


async def _wait(platform):
    while platform.scan.tasks:
        await asyncio.gather(*platform.scan.tasks.values(), return_exceptions=True)


async def test_profile_adds_identifiers_and_reaches_the_brief(client, platform):
    inv = (await client.post("/investigations", json={
        "name": "Vetting", "purpose": "Authorized pre-employment check"})).json()
    r = await client.post(f"/investigations/{inv['id']}/targets", json={
        "value": "Ion Popescu", "type": "Person", "scan": False,
        "profile": {"birth_year": 1985, "locations": ["Craiova"],
                    "emails": ["ion@example.com"], "usernames": ["@ionp"]}})
    assert r.status_code == 202, r.text
    targets = (await client.get(f"/investigations/{inv['id']}")).json()["targets"]
    assert {(t["type"], t["value"]) for t in targets} == {
        ("Person", "Ion Popescu"), ("Email", "ion@example.com"), ("Username", "ionp")}
    person = r.json()["entity_id"]
    # The identifiers are tied to the subject through the brief, cited like a source.
    f = platform.db.one("SELECT * FROM findings WHERE connector = ?", (identity.PROFILE_SOURCE,))
    edges = platform.db.query("SELECT type FROM edges e JOIN edge_sources s ON "
                              "s.edge_id = e.id WHERE s.finding_id = ? AND e.from_id = ?",
                              (f["id"], person))
    assert {e["type"] for e in edges} == {"uses_email", "has_username"}
    brief = identity.brief(platform.db, inv["id"])
    assert "born 1985" in brief and "Craiova" in brief

    # Updating the profile adds the new identifier and re-runs the search with context.
    tid = next(t["id"] for t in targets if t["type"] == "Person")
    r = await client.put(f"/investigations/{inv['id']}/targets/{tid}/profile", json={
        "profile": {"birth_year": 1985, "locations": ["Craiova"], "employers": ["Acme"],
                    "emails": ["ion@example.com", "ion@acme.example"]}})
    assert r.status_code == 200, r.text
    assert len(r.json()["identifier_entity_ids"]) == 1 and len(r.json()["run_ids"]) == 2
    await _wait(platform)
    args = {f["input"]["arg"] for f in platform.db.query(
        "SELECT input FROM findings WHERE connector = 'dork_search'")}
    assert '"Ion Popescu" "Acme"' in args

    r = await client.post(f"/investigations/{inv['id']}/targets", json={
        "value": "example.com", "scan": False, "profile": {"birth_year": 1990}})
    assert r.status_code == 422


async def test_common_name_warning_and_whos_who(client, platform, extra):
    inv = (await client.post("/investigations", json={
        "name": "Vetting", "purpose": "Authorized pre-employment check"})).json()
    r = await client.post(f"/investigations/{inv['id']}/targets", json={
        "value": "Ion Popescu", "type": "Person", "scan": False})
    person = r.json()["entity_id"]
    full = platform.db.get("investigations", inv["id"])
    await platform.ledger.run(full, "interpol", "ion popescu", user="t", run_by="t",
                              entity_id=person)
    await platform.ledger.run(full, "companies_house", "GB00000001", user="t", run_by="t")
    ov = (await client.get(f"/investigations/{inv['id']}/identity")).json()
    t = ov["targets"][0]
    assert (t["namesakes"], t["list_matches"], t["common_name"]) == (1, 2, True)

    # The verify agent groups the namesakes; the analyst picks the subject in one click.
    namesake = platform.db.one("SELECT id FROM entities WHERE key LIKE 'popescu ion @%'")["id"]
    box = ToolBox(platform.ledger, full, "ar_v", "t", budget=5)
    assert "set_identities" in {x["name"] for x in verify_tools(full, "all", [])}
    out, err = await box.call("set_identities", {"identities": [
        {"label": "Ion Popescu, dentist in Craiova", "entity_ids": [person],
         "distinguishing": "born 1985, Craiova", "is_subject": True},
        {"label": "Ion Popescu, UK footballer", "entity_ids": [namesake, "e_nope"],
         "distinguishing": "British, Acme Holdings director"}]})
    assert not err and '"recorded": 2' in out
    clusters = (await client.get(f"/investigations/{inv['id']}/identity")).json()["clusters"]
    subject = next(c for c in clusters if c["is_subject"])
    other = next(c for c in clusters if not c["is_subject"])
    r = await client.post(f"/identity-clusters/{subject['id']}/choose",
                          json={"reason": "born 1985, Craiova"})
    assert r.status_code == 200 and r.json()["rejected"] == 1
    assert platform.db.get("entities", namesake)["status"] == "rejected"
    assert platform.db.get("identity_clusters", subject["id"])["chosen"]
    assert not platform.db.get("identity_clusters", other["id"])["chosen"]
    assert "identified the subject as: Ion Popescu, dentist" in identity.brief(
        platform.db, inv["id"])


async def test_graph_nodes_carry_their_latest_verdict(client, platform, make_investigation):
    inv = (await client.post("/investigations", json={
        "name": "Vetting", "purpose": "Authorized pre-employment check"})).json()
    r = await client.post(f"/investigations/{inv['id']}/targets", json={
        "value": "jane@example.com", "scan": True})
    await _wait(platform)
    jdoe = platform.db.one("SELECT id FROM entities WHERE key = 'jdoe'")["id"]
    review.record(platform.db, platform.resolver, platform.bus, jdoe, "uncertain",
                  "can't open the profile", reviewer="ar_v", user="t", by_agent=True)
    nodes = {n["data"]["id"]: n["data"] for n in
             (await client.get(f"/investigations/{inv['id']}/graph")).json()["nodes"]}
    assert nodes[jdoe]["verdict"] == "uncertain"
    assert nodes[r.json()["entity_id"]]["verdict"] is None
    assert (await client.get(f"/entities/{jdoe}")).json()["verdict"] == "uncertain"


async def test_linkedin_captures_need_a_logged_in_persona(platform, make_investigation):
    inv = make_investigation()
    platform.resolver.ensure_entity(inv["id"], EntityType.SOCIAL_PROFILE, "linkedin:jane-doe")
    url = "https://www.linkedin.com/in/jane-doe"
    out = await platform.social.capture(inv, url, user="t", run_by="t", persona="none")
    assert out.refused and "login wall" in out.error
    out = await platform.social.capture(inv, url, user="t", run_by="t")
    assert out.refused and "No logged-in Linkedin research account" in out.error
