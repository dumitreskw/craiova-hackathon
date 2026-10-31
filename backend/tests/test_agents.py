"""Agent loop, agent tools and evidence capture, with a scripted fake Claude."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from app import review
from app.agent.catalog import build_model, fetch_venice_models, is_anthropic
from app.agent.model import VeniceModel, _openai_messages, _openai_tools, echo_content
from app.evidence import capture
from app.main import create_app
from app.models import new_id, now
from app.platform import Platform
from connectors.base import EntityType


def text(t):
    return SimpleNamespace(type="text", text=t)


def tool(tid, name, inp):
    return SimpleNamespace(type="tool_use", id=tid, name=name, input=inp)


def msg(*blocks, stop="end_turn", category=None):
    return SimpleNamespace(
        content=list(blocks), stop_reason=stop,
        stop_details=SimpleNamespace(category=category) if category else None,
        usage=SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=80,
                              cache_creation_input_tokens=0))


class FakeModel:
    """Plays back a script; a step may be a message or fn(messages) -> message."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    async def turn(self, *, system, tools, messages, effort, on_delta):
        self.requests.append({"system": system, "tools": [t["name"] for t in tools],
                              "n_messages": len(messages), "effort": effort,
                              "last": messages[-1]})
        step = self.script.pop(0)
        m = step(messages) if callable(step) else step
        for b in m.content:
            if b.type == "text":
                on_delta("text", b.text)
        return m


@pytest.fixture
def agent_platform(settings, fake_registry):
    holder = {}
    p = Platform(settings, model_factory=lambda: holder["model"])
    p.script = lambda *steps: holder.__setitem__("model", FakeModel(steps)) or holder["model"]
    yield p
    p.close()


def make_inv(p, **over):
    inv = {"id": new_id("inv"), "name": "Example SRL", "purpose": "Authorized vendor review",
           "mode": "passive",
           "settings": {"max_depth": 1}, "retention_days": 90,
           "legal_hold": 0, "created_by": "t", "created_at": now(), **over}
    p.db.insert("investigations", inv)
    return p.db.get("investigations", inv["id"])


def add_target(p, inv, value, etype, key):
    eid = p.resolver.ensure_entity(inv["id"], etype, key)
    p.db.insert("targets", {"id": new_id("t"), "investigation_id": inv["id"], "value": value,
                            "type": etype.value, "entity_id": eid, "added_by": "t",
                            "created_at": now()})
    return eid


async def run(p, inv, kind, budget=10):
    run_id = p.agents.start(inv, kind, "Map the footprint", "ana", budget)
    await p.agents.tasks[run_id]
    return p.db.get("agent_runs", run_id)


# --- collection agent ----------------------------------------------------------------


async def test_collection_agent_runs_tools_through_the_ledger(agent_platform):
    p = agent_platform
    inv = make_inv(p)
    add_target(p, inv, "example.com", EntityType.DOMAIN, "example.com")
    model = p.script(
        msg(text("Starting with DNS."), tool("t1", "dns", {"value": "Example.COM"}),
            tool("t2", "interpol", {"value": "Some Stranger"}), stop="tool_use"),
        msg(text("Done: example.com resolves to 203.0.113.10.")),
    )
    r = await run(p, inv, "collection")
    assert r["status"] == "finished" and "203.0.113.10" in r["summary"]
    f = p.db.one("SELECT * FROM findings WHERE connector = 'dns'")
    assert f["input"]["arg"] == "example.com" and f["run_by"] == r["id"]
    # Screening is not limited to targets: an associate's name is screened too.
    results = {b["tool_use_id"]: b for b in model.requests[1]["last"]["content"]}
    assert not results["t2"].get("is_error")
    assert p.db.query("SELECT * FROM findings WHERE connector = 'interpol'")
    # Passive investigation: the active connector and screenshot are not offered.
    offered = model.requests[0]["tools"]
    assert "dns" in offered and "http_probe" not in offered and "screenshot" not in offered
    assert offered == sorted(offered[:-2]) + ["get_entity", "graph_overview"]
    assert model.requests[0]["effort"] == "medium"
    assert r["stats"]["tool_calls"] == 2 and r["stats"]["cache_read_input_tokens"] == 160
    assert [t["t"] for t in r["transcript"]] == ["text", "tool", "tool", "result", "result", "text"]
    actions = {a["action"] for a in p.db.query("SELECT action FROM audit")}
    assert {"agent.collection.start", "agent.collection.finish", "connector.call"} <= actions


async def test_collection_agent_can_screen_its_own_targets(agent_platform):
    p = agent_platform
    inv = make_inv(p)
    add_target(p, inv, "Jane Doe", EntityType.PERSON, "jane doe")
    p.script(msg(tool("t1", "interpol", {"value": "Jane Doe", "notice_type": "red"}),
                 stop="tool_use"),
             msg(text("Two unverified name matches.")))
    r = await run(p, inv, "collection")
    assert r["status"] == "finished"
    assert p.db.query("SELECT * FROM entities WHERE status = 'candidate'")


async def test_tool_budget_is_enforced(agent_platform):
    p = agent_platform
    inv = make_inv(p)
    add_target(p, inv, "example.com", EntityType.DOMAIN, "example.com")
    model = p.script(msg(tool("a", "dns", {"value": "example.com"}),
                         tool("b", "crtsh", {"value": "example.com"}), stop="tool_use"),
                     msg(text("Summary.")))
    r = await run(p, inv, "collection", budget=1)
    assert r["stats"]["tool_calls"] == 1
    errors = [b for b in model.requests[1]["last"]["content"] if b.get("is_error")]
    assert len(errors) == 1 and "budget exhausted" in errors[0]["content"]


async def test_invalid_tool_input_is_returned_as_error(agent_platform):
    p = agent_platform
    inv = make_inv(p)
    add_target(p, inv, "example.com", EntityType.DOMAIN, "example.com")
    model = p.script(msg(tool("a", "dns", {"value": 42}), tool("b", "dns", "not-an-object"),
                         stop="tool_use"), msg(text("ok")))
    await run(p, inv, "collection")
    results = model.requests[1]["last"]["content"]
    assert all(b["is_error"] for b in results)
    assert "INVALID_JSON" in results[1]["content"]
    assert not p.db.query("SELECT * FROM findings")


async def test_refusal_stops_without_running_tools(agent_platform):
    p = agent_platform
    inv = make_inv(p)
    add_target(p, inv, "example.com", EntityType.DOMAIN, "example.com")
    p.script(msg(tool("a", "dns", {"value": "example.com"}), stop="refusal", category="cyber"))
    r = await run(p, inv, "collection")
    assert r["status"] == "refused" and "cyber" in r["summary"]
    assert not p.db.query("SELECT * FROM findings")


async def test_lookups_skip_entities_verified_as_not_the_subject(agent_platform):
    p = agent_platform
    inv = make_inv(p)
    add_target(p, inv, "Jane Doe", EntityType.PERSON, "jane doe")
    other = p.resolver.ensure_entity(inv["id"], EntityType.PERSON, "jane m doe")
    review.record(p.db, p.resolver, p.bus, other, "rejected", "A namesake in Ohio.",
                  reviewer="claude", user="t", by_agent=True)
    model = p.script(msg(tool("a", "interpol", {"value": "Jane M Doe"}),
                         tool("b", "interpol", {"value": "John Roe"}), stop="tool_use"),
                     msg(text("done")))
    await run(p, inv, "collection")
    results = {b["tool_use_id"]: b for b in model.requests[1]["last"]["content"]}
    assert results["a"]["is_error"] and "namesake in Ohio" in results["a"]["content"]
    assert not results["b"].get("is_error")


async def test_truncated_tool_call_is_not_executed(agent_platform):
    p = agent_platform
    inv = make_inv(p)
    add_target(p, inv, "example.com", EntityType.DOMAIN, "example.com")
    p.script(msg(tool("a", "dns", {"value": "exam"}), stop="max_tokens"))
    r = await run(p, inv, "collection")
    assert r["status"] == "failed" and not p.db.query("SELECT * FROM findings")


async def test_model_error_fails_the_run_cleanly(agent_platform):
    p = agent_platform
    inv = make_inv(p)

    def boom(_messages):
        raise RuntimeError("401 invalid x-api-key")

    p.script(boom)
    r = await run(p, inv, "collection")
    assert r["status"] == "failed" and "invalid x-api-key" in r["summary"]


def test_echo_content_drops_pre_fallback_tool_calls():
    blocks = [SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text="a"),
              SimpleNamespace(type="tool_use", id="x"), SimpleNamespace(type="fallback"),
              SimpleNamespace(type="tool_use", id="y")]
    kept = echo_content(blocks)
    assert [b.type for b in kept] == ["text", "fallback", "tool_use"] and kept[-1].id == "y"


# --- Venice model (OpenAI-compatible) -------------------------------------------------


def test_openai_message_and_tool_conversion():
    history = [
        {"role": "user", "content": "brief"},
        {"role": "assistant",
         "content": [SimpleNamespace(type="thinking", thinking="hmm"), text("On it."),
                     tool("t1", "dns", {"value": "example.com"})]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "203.0.113.10"}]},
        {"role": "assistant", "content": [text("Done.")]},
    ]
    out = _openai_messages("SYS", history)
    assert out[0] == {"role": "system", "content": "SYS"}
    assert out[1] == {"role": "user", "content": "brief"}
    assert out[2]["role"] == "assistant" and out[2]["content"] == "On it."
    assert out[2]["tool_calls"] == [{"id": "t1", "type": "function", "function": {
        "name": "dns", "arguments": '{"value": "example.com"}'}}]
    assert out[3] == {"role": "tool", "tool_call_id": "t1", "content": "203.0.113.10"}
    assert out[4] == {"role": "assistant", "content": "Done."}
    otools = _openai_tools([{"name": "dns", "description": "resolve",
                             "input_schema": {"type": "object"}}])
    assert otools == [{"type": "function", "function": {
        "name": "dns", "description": "resolve", "parameters": {"type": "object"}}}]


async def test_venice_model_streams_openai_sse():
    body = (
        'data: {"choices": [{"delta": {"content": "Pub"}}]}\n\n'
        'data: {"choices": [{"delta": {"content": "lic"}}]}\n\n'
        'data: {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", '
        '"function": {"name": "dns", "arguments": "{\\"value\\":"}}]}}]}\n\n'
        'data: {"choices": [{"delta": {"tool_calls": [{"index": 0, '
        '"function": {"arguments": " \\"example.com\\"}"}}]}, "finish_reason": "tool_calls"}]}\n\n'
        'data: {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 3}}\n\n'
        "data: [DONE]\n\n"
    )
    seen = {}

    def handler(request):
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, content=body.encode())

    deltas = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                               base_url="http://venice.test")
    model = VeniceModel(model="m", api_key="k", client=client)
    result = await model.turn(
        system="SYS", tools=[{"name": "dns", "description": "d", "input_schema": {}}],
        messages=[{"role": "user", "content": "brief"}], effort="low",
        on_delta=lambda kind, chunk: deltas.append(chunk))
    assert "".join(deltas) == "Public"
    assert [b.type for b in result.content] == ["text", "tool_use"]
    assert result.content[1].id == "call_1" and result.content[1].name == "dns"
    assert result.content[1].input == {"value": "example.com"}
    assert result.stop_reason == "tool_use"
    assert (result.usage.input_tokens, result.usage.output_tokens) == (12, 3)
    payload = seen["payload"]
    assert payload["model"] == "m" and payload["stream"]
    assert payload["messages"][0] == {"role": "system", "content": "SYS"}
    assert payload["tools"][0]["function"]["name"] == "dns"


async def test_venice_content_filter_maps_to_refusal():
    body = ('data: {"choices": [{"delta": {"content": "no"}, "finish_reason": "content_filter"}]}\n\n'
            "data: [DONE]\n\n")
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, content=body.encode())), base_url="http://venice.test")
    model = VeniceModel(model="m", api_key="k", client=client)
    result = await model.turn(system="", tools=[], messages=[{"role": "user", "content": "x"}],
                              effort="low", on_delta=lambda *a: None)
    assert result.stop_reason == "refusal"


async def test_venice_bad_tool_json_raises_valueerror_for_loop_retry():
    body = ('data: {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c", '
            '"function": {"name": "dns", "arguments": "{bad"}}]}, "finish_reason": "tool_calls"}]}\n\n'
            "data: [DONE]\n\n")
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, content=body.encode())), base_url="http://venice.test")
    model = VeniceModel(model="m", api_key="k", client=client)
    with pytest.raises(ValueError):  # the agent loop catches this and re-issues the turn
        await model.turn(system="", tools=[], messages=[{"role": "user", "content": "x"}],
                         effort="low", on_delta=lambda *a: None)


async def test_venice_http_error_raises():
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(401, content=b'{"error":"bad key"}')), base_url="http://venice.test")
    model = VeniceModel(model="m", api_key="k", client=client)
    with pytest.raises(RuntimeError, match="Venice 401"):
        await model.turn(system="", tools=[], messages=[{"role": "user", "content": "x"}],
                         effort="low", on_delta=lambda *a: None)


# --- model catalogue & assignments ----------------------------------------------------


async def test_fetch_venice_models_lists_tool_capable_first():
    data = {"object": "list", "data": [
        {"id": "no-tools", "type": "text",
         "model_spec": {"name": "No Tools", "capabilities": {"supportsFunctionCalling": False}}},
        {"id": "ds-flash", "type": "text", "context_length": 128000,
         "model_spec": {"name": "DeepSeek Flash", "description": "fast",
                        "capabilities": {"supportsFunctionCalling": True, "supportsReasoning": True}}},
    ]}
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json=data)), base_url="http://venice.test")
    entries = await fetch_venice_models("k", client=client)
    assert [e["id"] for e in entries] == ["ds-flash", "no-tools"]  # tool-capable first
    assert entries[0]["tools"] and entries[0]["reasoning"] and entries[0]["context"] == 128000
    assert entries[0]["provider"] == "venice" and entries[0]["label"] == "DeepSeek Flash"


def test_build_model_routes_by_provider():
    assert is_anthropic("claude-opus-5-5") and not is_anthropic("deepseek-v4-1-flash-1017")
    m = build_model("deepseek-v4-1-flash-1017", progress_updates=True, venice_api_key="k")
    assert isinstance(m, VeniceModel) and m.model == "deepseek-v4-1-flash-1017"
    with pytest.raises(RuntimeError, match="VENICE_INFERENCE_KEY"):
        build_model("some-venice-model", progress_updates=True, venice_api_key=None)


def test_platform_assignments_default_and_resolve(settings):
    s = replace(settings, venice_api_key="k")
    p = Platform(s)  # default (no injected factory) -> resolver active
    try:
        assert p.assigned_model("collection") == "claude-opus-5-5"
        assert p.assigned_model("worker") == "claude-sonnet-5-5"
        p.db.set_setting("model_assignments", {"worker": "deepseek-v4-1-flash-1017"}, "ana")
        assert p.assigned_model("worker") == "deepseek-v4-1-flash-1017"
        assert p.assigned_model("collection") == "claude-opus-5-5"  # untouched
        resolved = p._resolve_model("worker", None)
        assert isinstance(resolved, VeniceModel) and resolved.model == "deepseek-v4-1-flash-1017"
        # A per-run override wins over the assignment.
        assert p._resolve_model("worker", "other-venice").model == "other-venice"
    finally:
        p.close()


async def test_api_models_list_and_assign(client, agent_platform, monkeypatch):
    p = agent_platform
    monkeypatch.setenv("VENICE_INFERENCE_KEY", "k")  # make venice models assignable
    listing = (await client.get("/models")).json()
    assert [t["key"] for t in listing["tasks"]][:3] == ["collection", "deep", "worker"]
    assert listing["assignments"]["collection"] == "claude-opus-5-5"
    assert any(m["id"] == "claude-opus-5-5" for m in listing["models"])

    res = await client.put("/models/assignments",
                           json={"assignments": {"worker": "deepseek-v4-1-flash-1017"}})
    assert res.status_code == 200
    assert res.json()["assignments"]["worker"] == "deepseek-v4-1-flash-1017"
    assert p.db.get_setting("model_assignments")["worker"] == "deepseek-v4-1-flash-1017"
    # Clearing it falls back to the built-in default.
    await client.put("/models/assignments", json={"assignments": {"worker": ""}})
    assert (await client.get("/models")).json()["assignments"]["worker"] == "claude-sonnet-5-5"


async def test_api_assign_rejects_unusable_model(client, monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    res = await client.put("/models/assignments",
                           json={"assignments": {"collection": "claude-opus-5-5"}})
    assert res.status_code == 422 and "ANTHROPIC_API_KEY" in str(res.json()["detail"])


# --- report agent ---------------------------------------------------------------------


async def _collected(p):
    inv = make_inv(p)
    seed = add_target(p, inv, "example.com", EntityType.DOMAIN, "example.com")
    await p.ledger.run(inv, "dns", "example.com", user="t", run_by="t", entity_id=seed)
    return inv, p.db.one("SELECT id FROM findings")["id"]


async def test_report_agent_iterates_until_lint_passes(agent_platform):
    p = agent_platform
    inv, fid = await _collected(p)

    def fixed(_messages):
        body = {"title": "Example SRL", "summary": "s", "sections": [{"title": "Infra", "claims": [
            {"id": "c1", "text": "example.com resolves to 203.0.113.10.", "evidence": [fid],
             "status": "verified"}]}]}
        return msg(tool("r2", "submit_report", {"report": body}), stop="tool_use")

    model = p.script(
        msg(tool("g", "graph_overview", {}), tool("f", "get_finding", {"finding_id": fid}),
            stop="tool_use"),
        msg(tool("r1", "submit_report", {"report": {"title": "x", "summary": "s", "sections": [
            {"title": "Infra", "claims": [{"id": "c1", "text": "Hosted somewhere.",
                                           "evidence": [], "status": "verified"}]}]}}),
            stop="tool_use"),
        fixed,
        msg(text("Report submitted.")),
    )
    r = await run(p, inv, "report")
    assert r["status"] == "finished", r["summary"]
    first_submit = model.requests[2]["last"]["content"][0]
    assert '"ok": false' in first_submit["content"] and "uncited" in first_submit["content"]
    rep_row = p.db.get("reports", r["stats"]["report_id"])
    assert rep_row["agent_run_id"] == r["id"] and rep_row["body"]["sections"][0]["claims"][0][
        "evidence"] == [fid]
    assert model.requests[0]["effort"] == "high"
    assert "submit_report" in model.requests[0]["tools"] and "dns" not in model.requests[0]["tools"]


async def test_report_agent_without_a_clean_report_fails(agent_platform):
    p = agent_platform
    inv, _ = await _collected(p)
    p.script(msg(text("I could not write it.")))
    r = await run(p, inv, "report")
    assert r["status"] == "failed" and "No lint-clean report" in r["summary"]


async def test_report_agent_captures_pages_including_the_targets_own(agent_platform):
    p = agent_platform
    inv, _ = await _collected(p)
    model = p.script(
        msg(tool("a", "capture_evidence", {"url": "https://news.example/story"}),
            tool("b", "capture_evidence", {"url": "https://www.example.com/about"}),
            stop="tool_use"),
        msg(text("stop")))
    await run(p, inv, "report")
    results = {b["tool_use_id"]: b for b in model.requests[1]["last"]["content"]}
    assert not results["a"].get("is_error") and "finding_id" in results["a"]["content"]
    # The target's own public pages are a page view, allowed in passive mode.
    assert not results["b"].get("is_error") and "finding_id" in results["b"]["content"]


# --- evidence capture + API ------------------------------------------------------------------


async def test_capture_and_html_report_embed_screenshot(agent_platform, settings):
    p = agent_platform
    inv, _ = await _collected(p)
    out = await capture(p.ledger, inv, "https://news.example/story", user="t", run_by="analyst")
    assert out.finding_id and (settings.evidence_dir / inv["id"] / "screenshots").is_dir()
    from app import report as rep
    body = {"title": "R", "sections": [{"title": "S", "claims": [
        {"id": "c1", "text": "Covered in the press.", "evidence": [out.finding_id]}]}]}
    html = rep.render(p.db, inv, body, fmt="html", evidence_dir=settings.evidence_dir)
    assert "data:image/png;base64," in html and "Page capture" in html


@pytest.fixture
async def client(agent_platform):
    app = create_app(agent_platform)
    app.state.platform = agent_platform
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test/api/v1") as c:
        yield c


async def test_api_collect_and_report_agent(client, agent_platform):
    p = agent_platform
    inv = (await client.post("/investigations", json={
        "name": "Example", "purpose": "Authorized footprint review"})).json()
    assert (await client.post(f"/investigations/{inv['id']}/report-agent",
                              json={"goal": "Summarize"})).status_code == 422
    p.script(msg(tool("a", "dns", {"value": "example.com"}), stop="tool_use"), msg(text("done")))
    r = await client.post(f"/investigations/{inv['id']}/collect",
                          json={"goal": "Map the footprint", "target": "example.com"})
    assert r.status_code == 202
    await asyncio.gather(*p.agents.tasks.values())
    run = (await client.get(f"/agent-runs/{r.json()['run_id']}")).json()
    assert run["status"] == "finished" and run["kind"] == "collection"
    targets = (await client.get(f"/investigations/{inv['id']}")).json()["targets"]
    assert [t["value"] for t in targets] == ["example.com"]


async def test_api_capture_and_screenshot_download(client, agent_platform):
    inv = (await client.post("/investigations", json={
        "name": "Example", "purpose": "Authorized footprint review"})).json()
    r = await client.post(f"/investigations/{inv['id']}/capture",
                          json={"url": "https://news.example/story"})
    assert r.status_code == 200
    fid = r.json()["finding"]["id"]
    png = await client.get(f"/findings/{fid}/screenshot")
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"


async def test_api_agents_need_credentials(settings, fake_registry, monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    p = Platform(settings)
    app = create_app(p)
    app.state.platform = p
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test/api/v1") as c:
        inv = (await c.post("/investigations", json={
            "name": "E", "purpose": "Authorized footprint review"})).json()
        r = await c.post(f"/investigations/{inv['id']}/collect",
                         json={"goal": "Map it", "target": "example.com"})
        assert r.status_code == 503 and "ANTHROPIC_API_KEY" in r.text
    p.close()
