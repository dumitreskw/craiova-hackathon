"""Reports (lint + render) and the REST API end to end, offline."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app import report as rep
from app.main import create_app
from app.models import new_id, now
from connectors.base import EntityType


async def _scan_domain(platform, inv):
    seed = platform.resolver.ensure_entity(inv["id"], EntityType.DOMAIN, "example.com")
    platform.db.insert("targets", {"id": new_id("t"), "investigation_id": inv["id"],
                                   "value": "example.com", "type": "Domain", "entity_id": seed,
                                   "added_by": "t", "created_at": now()})
    await platform.scan.run("ar_x", inv, [seed], "t")
    return seed


def _rules(issues):
    return {i["rule"] for i in issues}


# --- report lint / render ----------------------------------------------------------


async def test_draft_from_graph_is_cited_and_lint_clean(platform, make_investigation):
    inv = make_investigation()
    await _scan_domain(platform, inv)
    body = rep.draft_from_graph(platform.db, inv)
    titles = [s["title"] for s in body["sections"]]
    related = body["sections"][titles.index("Related third-party assets (not investigated further)")]
    assert {"ns1.dnsprovider.net", "noc@dnsprovider.net"} <= {
        c["text"].split()[1] for c in related["claims"]}
    infra = body["sections"][titles.index("Infrastructure")]
    assert not any("dnsprovider" in c["text"] for c in infra["claims"])
    assert not [i for i in rep.lint(platform.db, inv["id"], body) if i["level"] == "error"]


async def test_draft_reports_legal_hits_as_unverified_and_no_match_as_fact(
        platform, make_investigation):
    inv = make_investigation()
    seed = platform.resolver.ensure_entity(inv["id"], EntityType.PERSON, "jane doe")
    await platform.ledger.run(inv, "interpol", "jane doe", user="t", run_by="t", entity_id=seed)
    await platform.ledger.run(inv, "europol", "jane doe", user="t", run_by="t", entity_id=seed)
    body = rep.draft_from_graph(platform.db, inv)
    legal = next(s for s in body["sections"] if s["title"].startswith("Sanctions"))
    statuses = sorted(c["status"] for c in legal["claims"])
    assert statuses == ["unverified", "unverified", "verified"]   # 2 hits + 1 "no match"
    assert not [i for i in rep.lint(platform.db, inv["id"], body) if i["level"] == "error"]
    md = rep.render(platform.db, inv, body, audience="client")
    assert "unverified possible match" in md


async def test_lint_catches_the_common_report_failures(platform, make_investigation):
    inv = make_investigation()
    await platform.ledger.run(inv, "interpol", "jane doe", user="t", run_by="t")
    legal_f = platform.db.one("SELECT id FROM findings WHERE connector = 'interpol'")["id"]
    body = {"title": "R", "sections": [{"title": "S", "claims": [
        {"id": "c1", "text": "Runs a blog.", "evidence": []},
        {"id": "c2", "text": "Jane Doe is wanted by INTERPOL.", "evidence": [legal_f]},
        {"id": "c3", "text": "Leaked login password: hunter2", "evidence": [legal_f],
         "status": "leaked"},
        {"id": "c4", "text": "Converted religion in 2005.", "evidence": [legal_f],
         "status": "unverified"},
        {"id": "c5", "text": "Captured via Mullvad egress, see evidence/x.png",
         "evidence": ["f_000000000000"]},
    ]}]}
    issues = rep.lint(platform.db, inv["id"], body, audience="client")
    by_claim = {(i["claim"], i["rule"]) for i in issues}
    assert ("c1", "uncited") in by_claim
    assert ("c2", "legal_assertion") in by_claim
    assert ("c3", "credential") in by_claim
    assert ("c4", "sensitive") in by_claim
    assert ("c5", "internal_detail") in by_claim and ("c5", "unknown_evidence") in by_claim


async def test_render_audiences(platform, make_investigation):
    inv = make_investigation()
    await _scan_domain(platform, inv)
    body = rep.draft_from_graph(platform.db, inv)
    internal = rep.render(platform.db, inv, body, audience="internal")
    client = rep.render(platform.db, inv, body, audience="client")
    assert "sha256" in internal and "Collection method" in internal and inv["id"] in internal
    for leak in ("sha256", "Collection method", inv["id"], "evidence/", "Legal basis"):
        assert leak not in client
    assert "[1]" in client and "## Evidence" in client
    uncited = rep.render(platform.db, inv, body, audience="client", cite=False)
    assert "] [1]" not in uncited and "- Domain" in uncited
    assert rep.render(platform.db, inv, body, fmt="html").startswith("<!doctype html>")


# --- API end to end --------------------------------------------------------------------


@pytest.fixture
async def client(platform):
    app = create_app(platform)
    app.state.platform = platform  # ASGITransport does not run the lifespan
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test/api/v1",
                                 headers={"X-Analyst": "ana"}) as c:
        yield c


async def _wait(platform):
    while platform.scan.tasks:
        await asyncio.gather(*platform.scan.tasks.values(), return_exceptions=True)


NEW_INV = {"name": "Example SRL", "purpose": "Vendor due diligence before contract",
           "legal_basis": "legitimate_interest"}


async def test_api_requires_purpose_and_authorization_for_active(client):
    r = await client.post("/investigations", json={**NEW_INV, "purpose": "x"})
    assert r.status_code == 422
    r = await client.post("/investigations", json={**NEW_INV, "mode": "active",
                                                   "scope": ["example.com"]})
    assert r.status_code == 422 and "authorization" in r.text
    r = await client.post("/investigations", json={**NEW_INV, "legal_basis": "because"})
    assert r.status_code == 422


async def test_api_end_to_end(client, platform):
    r = await client.post("/investigations", json=NEW_INV)
    assert r.status_code == 201
    inv_id = r.json()["id"]

    r = await client.post(f"/investigations/{inv_id}/targets", json={"value": "example.com"})
    assert r.status_code == 202 and r.json()["type"] == "Domain"
    run_id = r.json()["run_id"]
    await _wait(platform)
    assert (await client.get(f"/agent-runs/{run_id}")).json()["status"] == "finished"

    g = (await client.get(f"/investigations/{inv_id}/graph")).json()
    labels = {n["data"]["key"] for n in g["nodes"]}
    assert {"example.com", "dev.example.com", "203.0.113.10"} <= labels
    assert g["edges"]

    ent_id = next(n["data"]["id"] for n in g["nodes"] if n["data"]["key"] == "203.0.113.10")
    ent = (await client.get(f"/entities/{ent_id}")).json()
    assert ent["sources"] and ent["edges"]
    fid = ent["sources"][0]["id"]
    assert (await client.get(f"/findings/{fid}")).json()["payload"]["source"] == "dns"

    r = await client.post(f"/investigations/{inv_id}/reports", json={})
    assert r.status_code == 201
    report = r.json()
    assert not [i for i in report["lint"] if i["level"] == "error"]
    md = await client.get(f"/reports/{report['id']}/render", params={"audience": "client"})
    assert md.status_code == 200 and "example.com" in md.text

    # A report with an uncited claim is blocked from rendering until fixed.
    bad = {**report["body"], "sections": [{"title": "X", "claims": [
        {"id": "c1", "text": "Unsourced statement"}]}]}
    await client.put(f"/reports/{report['id']}", json={"body": bad})
    assert (await client.get(f"/reports/{report['id']}/render")).status_code == 409
    assert (await client.get(f"/reports/{report['id']}/render",
                             params={"force": True})).status_code == 200

    audit = (await client.get("/audit", params={"investigation_id": inv_id})).json()
    actions = {a["action"] for a in audit}
    assert {"investigation.create", "target.add", "connector.call", "report.create",
            "report.render"} <= actions
    assert all(a["user"] == "ana" for a in audit if a["action"] != "connector.call")


async def test_api_pivot_refuses_candidates_and_confirm_needs_reason(client, platform):
    inv_id = (await client.post("/investigations", json=NEW_INV)).json()["id"]
    await client.post(f"/investigations/{inv_id}/targets",
                      json={"value": "Jane Doe", "type": "Person"})
    await _wait(platform)
    cand = platform.db.one("SELECT id FROM entities WHERE status = 'candidate'")["id"]
    assert (await client.post(f"/entities/{cand}/pivot", json={})).status_code == 409
    assert (await client.post(f"/entities/{cand}/confirm", json={})).status_code == 422
    r = await client.post(f"/entities/{cand}/confirm",
                          json={"reason": "DOB matches passport copy"})
    assert r.json()["status"] == "confirmed" and r.json()["confidence"] == 1.0


async def test_api_delete_purges_and_respects_legal_hold(client, platform):
    inv_id = (await client.post("/investigations", json=NEW_INV)).json()["id"]
    await client.patch(f"/investigations/{inv_id}", json={"legal_hold": True})
    assert (await client.delete(f"/investigations/{inv_id}")).status_code == 409
    await client.patch(f"/investigations/{inv_id}", json={"legal_hold": False})
    assert (await client.delete(f"/investigations/{inv_id}")).status_code == 204
    assert (await client.get(f"/investigations/{inv_id}")).status_code == 404


async def test_api_switching_to_active_needs_authorization(client):
    inv_id = (await client.post("/investigations", json=NEW_INV)).json()["id"]
    r = await client.patch(f"/investigations/{inv_id}", json={"mode": "active"})
    assert r.status_code == 422
    r = await client.patch(f"/investigations/{inv_id}", json={
        "mode": "active", "scope": ["example.com"], "authorization": "SOW-42 signed 2026-10-30"})
    assert r.status_code == 200 and r.json()["mode"] == "active"


async def test_api_connectors_listing(client):
    names = {c["name"] for c in (await client.get("/connectors")).json()}
    assert "dns" in names
