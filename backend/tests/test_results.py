"""Better results: name variants, independent sources (test_app_core), the
choice feeding the profile, court / news / officer sources, avatar matching,
automatic verification and LinkedIn persona failover."""

from __future__ import annotations

import asyncio
import io
import time

import httpx
import pytest
import respx

from app import avatars, identity
from app.models import new_id, now
from connectors import ConnectorSpec
from connectors._names import matches_query, name_relation
from connectors.base import ConnectorResult, EntityCandidate, EntityType
from connectors.court_records import API as COURT_API
from connectors.court_records import court_city, court_records
from connectors.news import API as NEWS_API
from connectors.news import news
from connectors.opencorporates import API as OC_API
from connectors.opencorporates import opencorporates_officers
from tests.test_agents import FakeModel, msg, text

PERSON, ORG, SP = EntityType.PERSON, EntityType.ORGANIZATION, EntityType.SOCIAL_PROFILE


def _target(p, inv, value, etype, key, profile=None):
    eid = p.resolver.ensure_entity(inv["id"], etype, key)
    p.db.insert("targets", {"id": new_id("t"), "investigation_id": inv["id"], "value": value,
                            "type": etype.value, "entity_id": eid, "added_by": "t",
                            "created_at": now(), "profile": profile or {}})
    return eid


# --- 3. name variants --------------------------------------------------------------------


@pytest.mark.parametrize("a,b,rel", [
    ("Ion Popescu", "Popescu Ion", "same"),
    ("Ion Popescu", "Ion-Marian Popescu", "variant"),
    ("Ionuț Popescu", "Ion Popescu", "variant"),
    ("Alex Ionescu", "Alexandru Ionescu", "variant"),
    ("Попеску Ион", "Ion Popescu", "variant"),
    ("Maria Popescu", "Maria Ionescu Popescu", "variant"),
    ("Ion Popescu", "Ioana Popescu", None),
    ("Ion Popescu", "Ion Ionescu", None),
])
def test_name_relation(a, b, rel):
    assert name_relation(a, b) == rel


def test_list_filter_ignores_word_order():
    assert matches_query("Ion Popescu", "POPESCU ION")
    assert matches_query("vlad", "Ion Vlad")
    assert not matches_query("Ion Popescu", "Ionescu Maria")


async def variant_people(name: str) -> ConnectorResult:
    return ConnectorResult(source="hunter", passive=True, raw={},
                           entities=[EntityCandidate(PERSON, "ion-marian popescu",
                                                     {"title": "Ion-Marian Popescu"})])


async def test_name_variant_is_linked_not_merged(platform, make_investigation, fake_registry):
    fake_registry["hunter"] = ConnectorSpec("hunter", variant_people, EntityType.DOMAIN, True, "f")
    inv = make_investigation()
    person = _target(platform, inv, "Ion Popescu", PERSON, "ion popescu")
    await platform.ledger.run(inv, "hunter", "example.com", user="t", run_by="t")
    other = platform.db.one("SELECT id FROM entities WHERE key = 'ion-marian popescu'")["id"]
    edge = platform.db.one("SELECT * FROM edges WHERE type = 'possible_match'")
    assert (edge["from_id"], edge["to_id"]) == (person, other)
    assert edge["attributes"]["basis"] == "name variant"
    assert platform.db.get("entities", person)["attributes"] == {}


# --- 2. picking the subject feeds the profile -----------------------------------------------


async def test_choice_learns_facts_and_searches_again(platform, make_investigation):
    from app.api.investigations import CONTEXT_SEARCHES  # noqa: F401 - the re-search list
    inv = make_investigation()
    person = _target(platform, inv, "Ion Popescu", PERSON, "ion popescu", {"birth_year": 1985})
    org = platform.resolver.ensure_entity(inv["id"], ORG, "acme dental srl")
    platform.db.insert("identity_clusters", {
        "id": "ic_1", "investigation_id": inv["id"], "label": "Ion Popescu, dentist",
        "summary": "", "distinguishing": "Craiova dentist", "entity_ids": [person, org],
        "is_subject": True, "chosen": False, "created_at": now(),
        "facts": identity.clean_facts({"birth_year": 1990, "locations": ["Craiova"],
                                       "roles": ["dentist"], "bogus": 1})})
    out = identity.choose(platform.db, platform.resolver, platform.bus, "ic_1", "matches", "ana")
    profile = platform.db.one("SELECT profile FROM targets WHERE entity_id = ?", (person,))["profile"]
    assert profile["birth_year"] == 1985                    # never overwritten
    assert profile["locations"] == ["Craiova"] and profile["roles"] == ["dentist"]
    assert profile["employers"] == ["Acme Dental Srl"]       # from the subject's own entities
    assert out["refresh"] == [person]                         # the search context changed


# --- 4. sources -------------------------------------------------------------------------------

COURT_XML = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>
<CautareDosareResponse xmlns="portalquery.just.ro"><CautareDosareResult>
<Dosar><parti><DosarParte><nume>POPESCU ION</nume><calitateParte>Pârât</calitateParte></DosarParte>
<DosarParte><nume>BANCA X SA, J40/1/2000, PRIN REPREZENTANT LEGAL Y</nume><calitateParte>Reclamant</calitateParte></DosarParte></parti>
<sedinte><DosarSedinta><data>2024-05-02T00:00:00</data><solutie>Admite</solutie><solutieSumar>Admite cererea&#x1;</solutieSumar></DosarSedinta></sedinte>
<numar>123/215/2023</numar><data>2023-02-01T00:00:00</data><institutie>JudecatoriaCRAIOVA</institutie>
<departament>Civil</departament><categorieCazNume>Civil</categorieCazNume><stadiuProcesualNume>Fond</stadiuProcesualNume><obiect>pretenţii</obiect></Dosar>
<Dosar><parti><DosarParte><nume>IONESCU MARIA</nume><calitateParte>Reclamant</calitateParte></DosarParte></parti>
<numar>9/2/2022</numar><data>2022-01-01T00:00:00</data><institutie>TribunalulBUCURESTI</institutie><obiect>x</obiect></Dosar>
</CautareDosareResult></CautareDosareResponse></soap:Body></soap:Envelope>"""


@respx.mock
async def test_court_records_are_unverified_cases_with_their_city():
    respx.post(COURT_API).mock(return_value=httpx.Response(200, text=COURT_XML))
    r = await court_records("Ion Popescu")
    assert r.raw["matches"] == 1 and "Ion Popescu" not in str(r.raw["cases"][0]["party"]).title() \
        or r.raw["cases"][0]["party"] == "POPESCU ION"
    case = next(e for e in r.entities if e.type == EntityType.DOCUMENT)
    assert case.key == "just.ro:123/215/2023@JudecatoriaCRAIOVA"
    assert case.attributes["city"] == "Craiova" and case.attributes["last_hearing"]["solutie"] == "Admite"
    person = next(e for e in r.entities if e.type == PERSON)
    assert person.attributes["unverified"] and person.attributes["listing_url"] == case.key
    assert person.attributes["disambiguators"]["court_city"] == "Craiova"
    assert court_city("TribunalulBUCURESTI") == "Bucuresti"


@respx.mock
async def test_court_hit_becomes_a_candidate_next_to_the_subject(platform, make_investigation,
                                                                  fake_registry):
    respx.post(COURT_API).mock(return_value=httpx.Response(200, text=COURT_XML))
    fake_registry["court_records"] = ConnectorSpec("court_records", court_records, PERSON, True, "f")
    inv = make_investigation()
    person = _target(platform, inv, "Ion Popescu", PERSON, "ion popescu")
    await platform.ledger.run(inv, "court_records", "Ion Popescu", user="t", run_by="t",
                              entity_id=person)
    cand = platform.db.one("SELECT * FROM entities WHERE status = 'candidate'")
    assert cand["key"].startswith("popescu ion @ just.ro:")
    assert platform.db.one("SELECT 1 AS x FROM edges WHERE type = 'possible_match' AND "
                           "from_id = ? AND to_id = ?", (person, cand["id"]))


@respx.mock
async def test_news_from_gdelt_and_rate_limit_note():
    route = respx.get(NEWS_API).mock(return_value=httpx.Response(200, json={"articles": [
        {"url": "https://stiri.example.ro/a", "title": "Medic din Craiova", "domain":
         "stiri.example.ro", "language": "Romanian", "sourcecountry": "Romania",
         "seendate": "20261015T120000Z"}]}))
    import connectors.news as n
    n._last = 0.0
    r = await news('"Ion Popescu" "Craiova"')
    assert route.calls[0].request.url.params["query"] == '"Ion Popescu" "Craiova"'
    assert r.entities[0].key == "https://stiri.example.ro/a"
    assert r.entities[0].attributes["published"] == "2026-10-15"
    route.mock(return_value=httpx.Response(200, text="Please limit requests to one every 5 seconds"))
    n._last = 0.0
    r = await news("Ion Popescu")
    assert not r.entities and "did not search" in r.note


@respx.mock
async def test_opencorporates_officers_are_keyed_per_company(monkeypatch):
    monkeypatch.setenv("OPENCORPORATES_API_TOKEN", "x")
    respx.get(OC_API).mock(return_value=httpx.Response(200, json={"results": {
        "total_count": 2, "officers": [
            {"officer": {"name": "ION POPESCU", "position": "director", "nationality": "British",
                         "company": {"name": "ACME LTD", "company_number": "01234567",
                                     "jurisdiction_code": "gb"}}},
            {"officer": {"name": "ION POPESCU", "position": "administrator",
                         "company": {"name": "DENT SRL", "company_number": "J16/1/2020",
                                     "jurisdiction_code": "ro"}}}]}}))
    r = await opencorporates_officers("Ion Popescu")
    keys = {e.key for e in r.entities if e.type == PERSON}
    assert keys == {"ion popescu @ GB01234567", "ion popescu @ RO:J16/1/2020"}
    monkeypatch.delenv("OPENCORPORATES_API_TOKEN")
    assert "not set" in (await opencorporates_officers("x")).note


def test_scan_uses_the_new_sources():
    from app.scan import ARG, PIVOTS, arg_for
    assert {"court_records", "opencorporates", "news"} <= set(PIVOTS[PERSON])
    assert {"court_records_org", "news_org"} <= set(PIVOTS[ORG])
    ent = {"type": "Person", "key": "ion popescu", "label": "Ion Popescu", "attributes": {}}
    assert arg_for("news", ent, {"locations": ["Craiova"]}) == '"Ion Popescu" "Craiova"'
    assert ARG["court_records"](ent) == "Ion Popescu"


# --- 5. avatars ---------------------------------------------------------------------------------


def _png(pattern: int, size: int = 64) -> bytes:
    """A picture with structure (a gradient and a disc whose place depends on
    ``pattern``), like a face on a background."""
    from PIL import Image, ImageDraw
    img = Image.new("L", (size, size))
    img.putdata([int(255 * x / size) for y in range(size) for x in range(size)])
    draw = ImageDraw.Draw(img)
    r = size // 4
    cx, cy = (pattern * size // 13) % size, (pattern * size // 7) % size
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=255 if pattern % 2 else 0)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_dhash_survives_resizing_and_skips_blank_pictures():
    a, a_big, b = avatars.dhash(_png(3)), avatars.dhash(_png(3, 200)), avatars.dhash(_png(11))
    assert avatars.distance(a, a_big) <= avatars.MAX_DISTANCE
    assert avatars.distance(a, b) > avatars.MAX_DISTANCE
    from PIL import Image
    buf = io.BytesIO()
    Image.new("L", (40, 40), 200).save(buf, "PNG")
    assert avatars.dhash(buf.getvalue()) is None
    assert avatars.dhash(b"not an image") is None


async def profile_with_avatar(handle: str) -> ConnectorResult:
    return ConnectorResult(source="github_user", passive=True, raw={},
                           entities=[EntityCandidate(SP, f"site:{handle}", {
                               "avatar": f"https://img.example/{handle}.png"})])


@respx.mock
async def test_same_picture_links_two_accounts(platform, make_investigation, fake_registry):
    fake_registry["pics"] = ConnectorSpec("pics", profile_with_avatar, SP, True, "f")
    respx.get("https://img.example/a.png").mock(return_value=httpx.Response(
        200, content=_png(3), headers={"content-type": "image/png"}))
    respx.get("https://img.example/b.png").mock(return_value=httpx.Response(
        200, content=_png(3, 160), headers={"content-type": "image/png"}))
    respx.get("https://img.example/c.png").mock(return_value=httpx.Response(
        200, content=_png(11), headers={"content-type": "image/png"}))
    inv = make_investigation()
    for h in ("a", "b", "c"):
        await platform.ledger.run(inv, "pics", h, user="t", run_by="t")
    edges = platform.db.query("SELECT * FROM edges WHERE type = ?", (avatars.EDGE,))
    ends = {platform.db.get("entities", x)["key"] for e in edges for x in (e["from_id"], e["to_id"])}
    assert len(edges) == 1 and ends == {"site:a", "site:b"}
    assert platform.db.one("SELECT 1 AS x FROM edge_sources WHERE edge_id = ?", (edges[0]["id"],))


# --- 7. automatic verification ------------------------------------------------------------------


async def test_auto_verify_starts_after_a_scan_with_namesakes(settings, fake_registry):
    from app.platform import Platform
    holder = {"model": FakeModel([msg(text("Sorted."))])}
    p = Platform(settings, model_factory=lambda: holder["model"])
    try:
        inv_row = {"id": new_id("inv"), "name": "T", "purpose": "Authorized vetting check",
                   "mode": "passive", "settings": {"max_depth": 0, "auto_verify": True},
                   "retention_days": 90, "legal_hold": 0, "created_by": "t",
                   "created_at": now()}
        p.db.insert("investigations", inv_row)
        inv = p.db.get("investigations", inv_row["id"])
        person = _target(p, inv, "Jane Doe", PERSON, "jane doe")
        await p.scan.run("ar_s", inv, [person], "ana")     # interpol: two list matches
        assert p.auto_verify(inv, "ar_s", "finished", "ana") is None   # one is already running
        runs = p.db.query("SELECT * FROM agent_runs WHERE kind = 'verify'")
        assert len(runs) == 1
        await asyncio.gather(*p.agents.tasks.values())
        # Off unless the investigation opted in.
        p.db.update("investigations", inv["id"], {"settings": {"max_depth": 0}})
        assert p.auto_verify(inv, "ar_s", "finished", "ana") is None
    finally:
        p.close()


# --- 8. LinkedIn personas -----------------------------------------------------------------------


def _social_entity(p, inv_id, key):
    return p.resolver.ensure_entity(inv_id, SP, key)


def _body(persona, session="ok"):
    import base64
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
    return {"ok": True, "http_status": 200, "title": "Jane", "persona_used": persona,
            "session": session, "login_wall": session != "ok", "blocked": False,
            "profile": {}, "text": "", "links": [], "screenshot_b64": base64.b64encode(png).decode()}


async def test_linkedin_capture_fails_over_to_another_persona(platform, make_investigation):
    hub = platform.social
    hub.worker_seen = time.monotonic()
    for name in ("li-a", "li-b"):
        hub.create_persona(name, "linkedin", "de", None, "t")
        hub.db.update("personas", hub.persona(name)["id"], {"status": "ok",
                                                             "last_used": "1" if name == "li-b" else ""})
    inv = make_investigation()
    url = "https://www.linkedin.com/in/jane-doe/"
    _social_entity(platform, inv["id"], "linkedin:jane-doe")
    task = asyncio.create_task(hub.capture(inv, url, user="t", run_by="t"))
    job = await hub.next_job(wait=2)
    assert job["persona"]["name"] == "li-a"
    hub.complete(job["id"], _body("li-a", session="logged_out"))
    job2 = await hub.next_job(wait=2)
    assert job2["persona"]["name"] == "li-b"
    hub.complete(job2["id"], _body("li-b"))
    out = await asyncio.wait_for(task, 5)
    assert out.finding_id and hub.persona("li-a")["status"] == "needs_login"
    assert platform.db.one("SELECT 1 AS x FROM audit WHERE action = 'social_capture.failover'")


def test_linkedin_health_alerts(platform):
    hub = platform.social
    assert any("offline" in a["message"] for a in hub.health()["alerts"])
    hub.worker_seen = time.monotonic()
    assert any("No logged-in LinkedIn" in a["message"] for a in hub.health()["alerts"])
    hub.create_persona("li-a", "linkedin", "de", None, "t")
    hub.db.update("personas", hub.persona("li-a")["id"], {"status": "ok"})
    alerts = hub.health()["alerts"]
    assert alerts and alerts[0]["level"] == "warn" and "Only one" in alerts[0]["message"]
    hub.create_persona("li-b", "linkedin", "de", None, "t")
    hub.db.update("personas", hub.persona("li-b")["id"], {"status": "ok"})
    assert not hub.health()["alerts"]
    assert hub.status()["health"]["linkedin"]["ok"] == 2
