"""Analyst-supplied material: upload, conversion, graph, agents, withdrawal."""

from __future__ import annotations

import io
import json

import docx
import httpx
import pytest

import connectors
from app import report as rep
from app.main import create_app
from connectors import ConnectorSpec
from connectors.base import ConnectorResult, EntityCandidate, EntityType
from tests.test_agents import add_target, make_inv, msg, text, tool
from tests.test_deep import RoleModel


def make_pdf(line: str) -> bytes:
    """A one-page PDF with a text layer, built by hand (no PDF writer needed)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({line}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for i, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
              % (len(objects) + 1, xref))
    return out.getvalue()


def make_docx() -> bytes:
    d = docx.Document()
    d.add_heading("Subject dossier", 1)
    d.add_paragraph("Jane Doe is director of Example SRL, CUI RO12345678. "
                    "Contact: jane.doe@example.org.")
    t = d.add_table(rows=1, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text = "Role", "Director since 2019"
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


async def fake_web_read(url: str) -> ConnectorResult:
    return ConnectorResult(
        source="web_read", passive=True,
        raw={"url": url, "final_url": url, "title": "Press release",
             "markdown": "# Press\nExample SRL appoints Jane Doe.", "emails": [],
             "company_identifiers": []},
        entities=[EntityCandidate(EntityType.URL, url, {"title": "Press release"})])


@pytest.fixture
def client_for(fake_registry, monkeypatch):
    registry = {**fake_registry, "web_read": ConnectorSpec(
        "web_read", fake_web_read, EntityType.URL, True, "fake web_read")}
    monkeypatch.setattr(connectors, "REGISTRY", registry)

    async def make(p):
        app = create_app(p)
        app.state.platform = p
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test/api/v1")
    return make


async def test_upload_converts_files_into_cited_findings(platform, client_for):
    inv = make_inv(platform)
    dossier = make_docx()
    async with await client_for(platform) as c:
        files = [("files", ("dossier.docx", dossier, "application/octet-stream")),
                 ("files", ("extract.pdf", make_pdf("Registry extract RO12345678"),
                            "application/pdf")),
                 ("files", ("notes.md", b"# Leads\n- https://news.example/story", "text/markdown")),
                 ("files", ("tool.exe", b"MZ", "application/octet-stream"))]
        r = await c.post(f"/investigations/{inv['id']}/materials", files=files)
        assert r.status_code == 201, r.text
        body = r.json()
        by_name = {m["name"]: m for m in body["materials"]}
        assert set(by_name) == {"dossier.docx", "extract.pdf", "notes.md"}
        assert body["errors"][0]["status"] == 415           # the .exe, the rest still stored

        d = by_name["dossier.docx"]
        assert d["format"] == "docx" and d["digest"]["outline"] == ["Subject dossier"]
        assert d["digest"]["company_identifiers"] == ["RO12345678"]
        assert d["digest"]["emails"] == ["jane.doe@example.org"]
        full = (await c.get(f"/materials/{d['id']}")).json()
        assert "| Role | Director since 2019 |" in full["markdown"]   # tables survive as markdown
        assert "Registry extract RO12345678" in (
            await c.get(f"/materials/{by_name['extract.pdf']['id']}")).json()["markdown"]
        original = await c.get(f"/materials/{d['id']}/file")
        assert original.status_code == 200 and original.content == dossier

        f = platform.db.get("findings", d["finding_id"])
        assert f["connector"] == "analyst_material" and f["run_by"] == "analyst" and f["sha256"]
        keys = {(e["type"], e["key"]) for e in platform.db.query(
            "SELECT type, key FROM entities WHERE investigation_id = ?", (inv["id"],))}
        assert {("Email", "jane.doe@example.org"), ("Organization", "RO12345678"),
                ("URL", "https://news.example/story"), ("Document", d["sha256"])} <= keys

        listed = (await c.get(f"/investigations/{inv['id']}/materials")).json()
        assert all("file" not in m for m in listed)
        actions = {a["action"] for a in platform.db.query(
            "SELECT action FROM audit WHERE investigation_id = ?", (inv["id"],))}
        assert "material.add" in actions


async def test_same_bytes_are_stored_once(platform, client_for):
    inv = make_inv(platform)
    async with await client_for(platform) as c:
        data = b"Jane Doe met the board on 3 May."
        first = (await c.post(f"/investigations/{inv['id']}/materials",
                              files=[("files", ("a.txt", data, "text/plain"))])).json()
        second = (await c.post(f"/investigations/{inv['id']}/materials",
                               files=[("files", ("b.txt", data, "text/plain"))])).json()
        assert second["materials"][0]["duplicate"] is True
        assert second["materials"][0]["id"] == first["materials"][0]["id"]
        only = await c.post(f"/investigations/{inv['id']}/materials",
                            files=[("files", ("x.exe", b"MZ", "x"))])
        assert only.status_code == 415


async def test_text_notes_and_links(platform, client_for):
    inv = make_inv(platform)
    add_target(platform, inv, "example.com", EntityType.DOMAIN, "example.com")
    async with await client_for(platform) as c:
        note = await c.post(f"/investigations/{inv['id']}/materials/text",
                            json={"title": "Client brief", "text": "Check RO12345678."})
        assert note.status_code == 201 and note.json()["kind"] == "text"
        link = await c.post(f"/investigations/{inv['id']}/materials/link",
                            json={"url": "https://press.example/jane"})
        assert link.status_code == 201, link.text
        m = link.json()
        assert m["kind"] == "link" and m["name"] == "Press release"
        assert platform.db.get("findings", m["finding_id"])["connector"] == "web_read"
        # The target's own public pages are a page view, allowed in passive mode.
        own = await c.post(f"/investigations/{inv['id']}/materials/link",
                           json={"url": "https://example.com/about"})
        assert own.status_code == 201, own.text


async def test_withdraw_removes_text_files_and_what_only_it_found(platform, client_for):
    inv = make_inv(platform)
    add_target(platform, inv, "jane.doe@example.org", EntityType.EMAIL, "jane.doe@example.org")
    async with await client_for(platform) as c:
        up = (await c.post(f"/investigations/{inv['id']}/materials",
                           files=[("files", ("d.docx", make_docx(), "x"))])).json()
        m = up["materials"][0]
        payload = platform.settings.data_dir / platform.db.get(
            "findings", m["finding_id"])["raw_path"]
        stored = platform.settings.evidence_dir / inv["id"] / "materials"
        assert payload.is_file() and any(stored.iterdir())

        # Cited by a report: refused.
        platform.db.insert("reports", {"id": "rep_1", "investigation_id": inv["id"],
                                       "title": "R", "body": {"sections": [{"claims": [
                                           {"evidence": [m["finding_id"]]}]}]},
                                       "created_by": "t", "created_at": "x", "updated_at": "x"})
        assert (await c.delete(f"/materials/{m['id']}")).status_code == 409
        platform.db.conn.execute("DELETE FROM reports")

        r = await c.delete(f"/materials/{m['id']}")
        assert r.status_code == 200 and r.json()["entities_removed"] >= 2
        assert not payload.exists() and not any(stored.iterdir())
        f = platform.db.get("findings", m["finding_id"])
        assert f["error"] == "withdrawn by analyst" and f["raw_path"] is None
        keys = {e["key"] for e in platform.db.query(
            "SELECT key FROM entities WHERE investigation_id = ?", (inv["id"],))}
        assert "RO12345678" not in keys                 # only the material named it
        assert "jane.doe@example.org" in keys           # a target always stays
        assert (await c.get(f"/investigations/{inv['id']}/materials")).json() == []


def test_lint_warns_when_a_verified_claim_rests_only_on_supplied_material(platform):
    inv = make_inv(platform)
    platform.db.insert("findings", {"id": "f_000000000001", "investigation_id": inv["id"],
                                    "connector": "analyst_material", "input": {"arg": "a.pdf"},
                                    "run_by": "analyst", "passive": 1, "fetched_at": "x",
                                    "duration_ms": 0})
    body = {"sections": [{"title": "S", "claims": [
        {"id": "c1", "text": "Jane Doe is a director.", "evidence": ["f_000000000001"],
         "status": "verified"},
        {"id": "c2", "text": "Jane Doe is a director.", "evidence": ["f_000000000001"],
         "status": "unverified"}]}]}
    issues = rep.lint(platform.db, inv["id"], body)
    assert [(i["rule"], i["claim"]) for i in issues] == [("supplied_only", "c1")]


# --- agents ------------------------------------------------------------------------


@pytest.fixture
def deep_platform(settings, fake_registry):
    from app.platform import Platform

    holder = {}
    p = Platform(settings, model_factory=lambda: holder["model"])
    p.model = lambda scripts: holder.__setitem__("model", RoleModel(scripts)) or holder["model"]
    yield p
    p.close()


async def test_orchestrator_reads_material_and_hands_it_to_one_worker(deep_platform):
    from app import materials as mat

    p = deep_platform
    inv = make_inv(p)
    add_target(p, inv, "example.com", EntityType.DOMAIN, "example.com")
    dossier, _ = await mat.add_file(p.ledger, inv, "dossier.docx", make_docx(), user="t")
    other, _ = await mat.add_text(p.ledger, inv, "Unrelated", "Board minutes " * 50 + "TAIL-ONLY",
                                   user="t")

    def orchestrate(messages):
        return msg(tool("rm", "read_material", {"material_id": dossier["id"]}), stop="tool_use")

    def dispatch(messages):
        return msg(tool("d1", "dispatch_worker", {
            "worker": "company_registry", "budget": 3, "materials": [dossier["id"]],
            "goal": "Confirm RO12345678 (Example SRL) and Jane Doe as director"}),
            tool("d2", "dispatch_worker", {"worker": "infrastructure", "budget": 3,
                                           "goal": "Map example.com"}),
            stop="tool_use")

    def worker(messages):
        brief = messages[0]["content"]
        if len(messages) == 1 and "company_registry" in brief:
            return msg(tool("w1", "read_material", {"material_id": dossier["id"]}),
                       tool("w2", "read_material", {"material_id": other["id"]}),
                       stop="tool_use")
        return msg(text("Worker done."))

    model = p.model({"orchestrator": [orchestrate, dispatch, msg(text("Synthesis."))],
                     "worker": [worker, worker, worker],
                     "report": [msg(text("no report"))]})
    run_id = p.agents.start(inv, "deep", "Vendor due diligence", "ana", 20)
    await p.agents.tasks[run_id]

    orch = [s for s in model.seen if s["role"] == "orchestrator"]
    brief = orch[0]["brief"]
    assert dossier["id"] in brief and "RO12345678" in brief and "Subject dossier" in brief
    assert "TAIL-ONLY" not in brief                              # a digest, not the text
    assert "read_material" in orch[0]["tools"]
    read = json.loads(orch[1]["last"]["content"][0]["content"])
    assert "Director since 2019" in read["text"] and read["next_offset"] is None
    assert read["finding_id"] == dossier["finding_id"]

    workers = [s for s in model.seen if s["role"] == "worker"]
    registry = next(s for s in workers if "company_registry" in s["brief"])
    infra = next(s for s in workers if "infrastructure" in s["brief"])
    assert "read_material" in registry["tools"] and dossier["id"] in registry["brief"]
    assert other["id"] not in registry["brief"]
    assert "read_material" not in infra["tools"] and dossier["id"] not in infra["brief"]
    results = next(s for s in workers if s is not registry and "company_registry" in s["brief"]
                   )["last"]["content"]
    ok, refused = results
    assert not ok.get("is_error") and "Director since 2019" in ok["content"]
    assert refused["is_error"] and "was not handed to this worker" in refused["content"]
