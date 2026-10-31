"""Deep analysis: orchestrator -> parallel workers -> verify -> report -> review (fake Claude)."""

from __future__ import annotations

import asyncio

import pytest

from app.agent import prompts
from app.platform import Platform
from connectors.base import EntityType
from tests.test_agents import add_target, make_inv, msg, text, tool


class RoleModel:
    """Answers by role (orchestrator / worker / report), recording what each saw."""

    def __init__(self, scripts):
        self.scripts = {k: list(v) for k, v in scripts.items()}
        self.seen: list[dict] = []
        self.active = 0
        self.max_parallel = 0

    async def turn(self, *, system, tools, messages, effort, on_delta):
        role = ("orchestrator" if system == prompts.ORCHESTRATOR else
                "worker" if system == prompts.WORKER else
                "verify" if system == prompts.VERIFY else
                "review" if system == prompts.REVIEWER else
                "report" if system == prompts.REPORT else "other")
        self.seen.append({"role": role, "tools": [t["name"] for t in tools],
                          "brief": messages[0]["content"], "last": messages[-1]})
        self.active += 1
        self.max_parallel = max(self.max_parallel, self.active)
        await asyncio.sleep(0.01)  # let parallel workers overlap
        self.active -= 1
        step = self.scripts[role].pop(0)
        return step(messages) if callable(step) else step


@pytest.fixture
def deep_platform(settings, fake_registry):
    holder = {}
    p = Platform(settings, model_factory=lambda: holder["model"])
    p.model = lambda scripts: holder.__setitem__("model", RoleModel(scripts)) or holder["model"]
    yield p
    p.close()


def worker_step(messages):
    brief = messages[0]["content"]
    if len(messages) == 1:  # first turn: one lookup within the workstream
        name = "dns" if "infrastructure" in brief else "interpol"
        value = "example.com" if name == "dns" else "Jane Doe"
        return msg(tool("w1", name, {"value": value}), stop="tool_use")
    return msg(text(f"Worker done ({'infra' if 'infrastructure' in brief else 'legal'})."))


async def _fid(p):
    return p.db.one("SELECT id FROM findings WHERE connector = 'dns'")["id"]


async def test_deep_analysis_runs_workers_in_parallel_then_reports(deep_platform):
    p = deep_platform
    inv = make_inv(p)
    add_target(p, inv, "example.com", EntityType.DOMAIN, "example.com")
    add_target(p, inv, "Jane Doe", EntityType.PERSON, "jane doe")

    def report_submit(_messages, text_="example.com resolves to 203.0.113.10 and 10.0.0.1."):
        fid = p.db.one("SELECT id FROM findings WHERE connector = 'dns'")["id"]
        body = {"title": "Deep", "summary": "s", "sections": [{"title": "Infra", "claims": [
            {"id": "c1", "text": text_, "evidence": [fid], "status": "verified"}]}]}
        return msg(tool("r", "submit_report", {"report": body}), stop="tool_use")

    def verdicts(_messages):
        noise = p.db.one("SELECT id FROM entities WHERE key = 'noc@dnsprovider.net'")["id"]
        hits = [r["id"] for r in p.db.query("SELECT id FROM entities WHERE status = 'candidate'")]
        return msg(tool("v", "set_verdicts", {"verdicts": [
            {"entity_id": noise, "verdict": "rejected", "reason": "DNS provider's NOC mailbox"},
            *[{"entity_id": h, "verdict": "rejected", "reason": "listing gives another DOB"}
              for h in hits]]}), stop="tool_use")

    def review_submit(_messages):
        return msg(tool("q", "submit_review", {"assessment": "One overstatement.", "problems": [
            {"claim_id": "c1", "kind": "unsupported", "detail": "DNS gives one address only",
             "fix": "drop 10.0.0.1"}]}), stop="tool_use")

    model = p.model({
        "orchestrator": [
            msg(tool("d1", "dispatch_worker", {"worker": "infrastructure",
                                               "goal": "Map the domain", "budget": 5}),
                tool("d2", "dispatch_worker", {"worker": "legal_screening",
                                               "goal": "Screen Jane Doe", "budget": 5}),
                stop="tool_use"),
            msg(text("Synthesis: infra mapped, two unverified name matches.")),
        ],
        "worker": [worker_step, worker_step, worker_step, worker_step],
        "verify": [msg(tool("q", "review_queue", {}), stop="tool_use"), verdicts,
                   msg(text("Subject identified; DNS provider noise and both name matches "
                            "rejected."))],
        "report": [report_submit, msg(text("Report submitted.")),
                   lambda m: report_submit(m, "example.com resolves to 203.0.113.10."),
                   msg(text("Fixed."))],
        "review": [review_submit, msg(text("One claim overstated."))],
    })
    run_id = p.agents.start(inv, "deep", "Profile the target", "ana", 20)
    await p.agents.tasks[run_id]
    run = p.db.get("agent_runs", run_id)
    assert run["status"] == "finished", run["summary"]
    assert model.max_parallel >= 2                       # the two workers overlapped

    workers = p.db.query("SELECT * FROM agent_runs WHERE kind = 'worker'")
    assert {w["stats"]["worker"] for w in workers} == {"infrastructure", "legal_screening"}
    assert all(w["stats"]["parent"] == run_id for w in workers)
    assert all(w["status"] == "finished" for w in workers)

    worker_tools = [s["tools"] for s in model.seen if s["role"] == "worker"]
    infra = next(t for t in worker_tools if "dns" in t)
    legal = next(t for t in worker_tools if "interpol" in t)
    assert "interpol" not in infra and "dns" not in legal  # each worker keeps to its lane

    orch_tools = next(s["tools"] for s in model.seen if s["role"] == "orchestrator")
    assert orch_tools == ["dispatch_worker", "graph_overview"]

    # Orchestrator saw both worker summaries; the report agent saw the notes.
    orch_second = [s for s in model.seen if s["role"] == "orchestrator"][1]["last"]["content"]
    assert len(orch_second) == 2 and all("Worker done" in r["content"] for r in orch_second)
    report_brief = next(s["brief"] for s in model.seen if s["role"] == "report")
    assert report_brief.startswith("# Dossier") and "### worker:" in report_brief
    assert "### deep:" in report_brief and "### verify:" in report_brief
    # Verification ran before the writer, and its rejections reach the dossier.
    assert "Rejected (not the subject)" in report_brief and "noc@dnsprovider.net" in report_brief
    verify_tools = next(s["tools"] for s in model.seen if s["role"] == "verify")
    assert {"review_queue", "set_verdicts", "dork_search", "get_finding"} <= set(verify_tools)
    assert p.db.one("SELECT status FROM entities WHERE key = 'noc@dnsprovider.net'")[
        "status"] == "rejected"
    assert not p.db.query("SELECT id FROM entities WHERE status = 'candidate'")
    # The reviewer saw each claim with its source, the writer got its problems back.
    review_brief = next(s["brief"] for s in model.seen if s["role"] == "review")
    assert "[c1] (verified)" in review_brief and "cites [f_" in review_brief
    writer_msgs = [s["last"] for s in model.seen if s["role"] == "report"]
    assert any("fact-checker" in str(m["content"]) for m in writer_msgs)

    assert run["stats"]["report_id"] and run["stats"]["report_run"]
    report = p.db.get("reports", run["stats"]["report_id"])
    assert report["agent_run_id"] == run["stats"]["report_run"]
    assert report["body"]["sections"][0]["claims"][0]["text"].endswith("203.0.113.10.")
    assert report["body"]["qa"]["problems"][0]["kind"] == "unsupported"
    writer = p.db.get("agent_runs", run["stats"]["report_run"])
    assert writer["stats"]["verify_run"] and writer["stats"]["review_problems"] == 1
    # Budget: 2 lookups were spent of 10 allocated; the rest went back to the pool.
    assert run["stats"]["tool_calls"] == 2


async def test_dispatch_rejects_unknown_worker_and_spent_budget(deep_platform):
    p = deep_platform
    inv = make_inv(p)
    add_target(p, inv, "example.com", EntityType.DOMAIN, "example.com")
    model = p.model({
        "orchestrator": [
            msg(tool("a", "dispatch_worker", {"worker": "hacking", "goal": "x", "budget": 3}),
                stop="tool_use"),
            msg(text("done")),
        ],
        "worker": [], "report": [msg(text("no report"))],
    })
    run_id = p.agents.start(inv, "deep", "Profile", "ana", 10)
    await p.agents.tasks[run_id]
    err = [s for s in model.seen if s["role"] == "orchestrator"][1]["last"]["content"][0]
    assert err["is_error"] and "worker must be one of" in err["content"]
    assert not p.db.query("SELECT * FROM agent_runs WHERE kind = 'worker'")


def test_worker_toolsets_only_name_real_tools():
    import connectors
    from app.agent.tools import WORKERS
    known = set(connectors.REGISTRY) | {"read_page", "social_capture", "social_search"}
    for name, spec in WORKERS.items():
        missing = set(spec["tools"]) - known
        assert not missing, f"{name}: unknown tools {missing}"
