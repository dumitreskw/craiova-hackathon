"""Deep analysis: orchestrator -> parallel workers -> report agent (fake Claude)."""

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

    def report_submit(_messages):
        fid = p.db.one("SELECT id FROM findings WHERE connector = 'dns'")["id"]
        body = {"title": "Deep", "summary": "s", "sections": [{"title": "Infra", "claims": [
            {"id": "c1", "text": "example.com resolves to 203.0.113.10.", "evidence": [fid],
             "status": "verified"}]}]}
        return msg(tool("r", "submit_report", {"report": body}), stop="tool_use")

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
        "report": [report_submit, msg(text("Report submitted."))],
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
    assert "Worker agent notes" in report_brief and "Deep agent notes" in report_brief

    assert run["stats"]["report_id"] and run["stats"]["report_run"]
    assert p.db.get("reports", run["stats"]["report_id"])["agent_run_id"] == run["stats"]["report_run"]
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
    known = set(connectors.REGISTRY) | {"read_page"}
    for name, spec in WORKERS.items():
        missing = set(spec["tools"]) - known
        assert not missing, f"{name}: unknown tools {missing}"
