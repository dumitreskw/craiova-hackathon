"""Entity verdicts (app/review.py), the verify agent's tools, persona tools and
the subject-aware draft."""

from __future__ import annotations

import httpx
import pytest

from app import report as rep
from app import review
from app.agent.tools import ToolBox, verify_tools
from app.ledger import RunOutcome
from app.main import create_app
from app.platform import Platform
from connectors.base import EntityType
from tests.test_agents import FakeModel, add_target, make_inv, msg, text, tool


@pytest.fixture
def agent_platform(settings, fake_registry):
    holder = {}
    p = Platform(settings, model_factory=lambda: holder["model"])
    p.script = lambda *steps: holder.__setitem__("model", FakeModel(steps)) or holder["model"]
    yield p
    p.close()


async def _person_case(p):
    """A person target, a DNS scan of their domain, and a wanted-list name match."""
    inv = make_inv(p)
    person = add_target(p, inv, "Jane Doe", EntityType.PERSON, "jane doe")
    await p.ledger.run(inv, "dns", "example.com", user="t", run_by="t")
    await p.ledger.run(inv, "interpol", "jane doe", user="t", run_by="t", entity_id=person)
    await p.ledger.run(inv, "gravatar", "jane@example.com", user="t", run_by="t")
    return inv, person


def _id(p, key):
    return p.db.one("SELECT id FROM entities WHERE key = ?", (key,))["id"]


def _record(p, eid, verdict, by_agent=True, reviewer="ar_x"):
    return review.record(p.db, p.resolver, p.bus, eid, verdict, "because", reviewer=reviewer,
                         user="ana", by_agent=by_agent)


async def test_verdict_rules(agent_platform):
    p = agent_platform
    inv, person = await _person_case(p)
    noc = _id(p, "noc@dnsprovider.net")
    _record(p, noc, "rejected")
    assert p.db.get("entities", noc)["status"] == "rejected"
    # The analyst overrules the agent, and the agent can't overrule the analyst back.
    _record(p, noc, "confirmed", by_agent=False, reviewer="ana")
    with pytest.raises(review.ReviewError, match="analyst"):
        _record(p, noc, "rejected")
    assert p.db.get("entities", noc)["status"] == "confirmed"
    # A wanted-list name match: the agent may reject or doubt it, never confirm it.
    hit = p.db.one("SELECT id FROM entities WHERE status = 'candidate'")["id"]
    with pytest.raises(review.ReviewError, match="only an analyst"):
        _record(p, hit, "confirmed")
    _record(p, hit, "uncertain")
    assert p.db.get("entities", hit)["status"] == "candidate"     # uncertain keeps the status
    with pytest.raises(review.ReviewError, match="target"):
        _record(p, person, "rejected")
    s = review.summary(p.db, inv["id"])
    assert s["confirmed"] == 1 and s["uncertain"] == 1 and s["rejected"] == 0


async def test_queue_is_subject_aware(agent_platform):
    p = agent_platform
    inv, _person = await _person_case(p)
    q = review.queue(p.db, inv["id"], limit=200)
    keys = {i["key"] for i in q["items"]}
    assert q["subject_kind"] == "person"
    assert "203.0.113.10" not in keys                   # IPs are plumbing for a person
    assert "jane doe" not in keys                       # the target itself is not judged
    assert q["items"][0]["status"] == "candidate"       # name matches come first
    assert {"noc@dnsprovider.net", "jdoe"} <= keys
    assert q["items"][0]["sources"][0]["connector"] == "interpol"
    _record(p, _id(p, "jdoe"), "rejected")
    assert "jdoe" not in {i["key"] for i in review.queue(p.db, inv["id"], limit=200)["items"]}


async def test_set_verdicts_tool_reports_errors_per_item(agent_platform):
    p = agent_platform
    inv, _person = await _person_case(p)
    box = ToolBox(p.ledger, inv, "ar_v", "ana", budget=5)
    hit = p.db.one("SELECT id FROM entities WHERE status = 'candidate'")["id"]
    out, err = await box.call("set_verdicts", {"verdicts": [
        {"entity_id": _id(p, "jdoe"), "verdict": "rejected", "reason": "different person"},
        {"entity_id": hit, "verdict": "confirmed", "reason": "same name"},
        {"entity_id": "e_nope", "verdict": "rejected", "reason": "x"}]})
    assert not err and '"rejected": 1' in out and "only an analyst" in out and "e_nope" in out
    assert box.verdicts == 1
    audit = p.db.one("SELECT detail FROM audit WHERE action = 'entity.rejected'")
    assert audit["detail"]["by"] == "ar_v"


class FakeSocial:
    def __init__(self, finding_id):
        self.finding_id = finding_id
        self.searched = []

    def platforms_ready(self):
        return ["linkedin"]

    def search(self, investigation, platform, query, *, user, run_by):
        self.searched.append((platform, query, run_by))
        return {"id": "sj_1"}

    async def wait(self, job_id, timeout=240):
        return RunOutcome(self.finding_id, None)


async def test_persona_tools_for_the_verify_agent(agent_platform):
    p = agent_platform
    inv, _person = await _person_case(p)
    assert "social_search" not in [t["name"] for t in verify_tools(inv, "all", [])]
    names = [t["name"] for t in verify_tools(inv, "all", ["linkedin"])]
    assert {"social_search", "social_capture", "review_queue", "set_verdicts"} <= set(names)
    fid = p.db.one("SELECT id FROM findings WHERE connector = 'gravatar'")["id"]
    box = ToolBox(p.ledger, inv, "ar_v", "ana", budget=2, social=FakeSocial(fid))
    out, err = await box.call("social_search", {"platform": "linkedin", "query": "Jane Doe"})
    assert not err and fid in out and box.calls == 1 and box.findings == [fid]
    assert box.social.searched == [("linkedin", "Jane Doe", "ar_v")]
    out, err = await box.call("social_search", {"platform": "x", "query": "Jane Doe"})
    assert err and "linkedin" in out


async def test_draft_for_a_person_leaves_out_what_is_not_verified(agent_platform):
    p = agent_platform
    inv, _person = await _person_case(p)
    body = rep.draft_from_graph(p.db, inv)
    titles = [s["title"] for s in body["sections"]]
    assert "Infrastructure" not in titles and "Related third-party assets" not in str(titles)
    assert "left out" in body["outstanding"][0]
    assert "jdoe" not in str(body["sections"])
    _record(p, _id(p, "jdoe"), "confirmed", by_agent=False, reviewer="ana")
    body = rep.draft_from_graph(p.db, inv)
    assert "jdoe" in str(body["sections"])
    assert not [i for i in rep.lint(p.db, inv["id"], body) if i["level"] == "error"]


@pytest.fixture
async def client(agent_platform):
    app = create_app(agent_platform)
    app.state.platform = agent_platform
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test/api/v1",
                                 headers={"X-Analyst": "ana"}) as c:
        yield c


async def test_api_verify_and_reviews(client, agent_platform):
    p = agent_platform
    inv, _person = await _person_case(p)
    noc = _id(p, "noc@dnsprovider.net")
    p.script(msg(tool("v", "set_verdicts", {"verdicts": [
        {"entity_id": noc, "verdict": "uncertain", "reason": "can't tell"}]}), stop="tool_use"),
        msg(text("One left uncertain.")))
    r = await client.post(f"/investigations/{inv['id']}/verify", json={})
    assert r.status_code == 202 and r.json()["kind"] == "verify"
    await p.agents.tasks[r.json()["run_id"]]
    run = p.db.get("agent_runs", r.json()["run_id"])
    assert run["status"] == "finished" and run["stats"]["verdicts"] == 1
    reviews = (await client.get(f"/investigations/{inv['id']}/reviews")).json()
    assert reviews["uncertain"] == 1 and reviews["uncertain_items"][0]["entity_id"] == noc
    # An analyst confirmation is recorded as a review and shown on the entity.
    await client.post(f"/entities/{noc}/confirm", json={"reason": "their own mailbox"})
    ent = (await client.get(f"/entities/{noc}")).json()
    assert ent["status"] == "confirmed" and ent["reviews"][-1]["reviewer"] == "ana"
    assert not ent["reviews"][-1]["by_agent"]
