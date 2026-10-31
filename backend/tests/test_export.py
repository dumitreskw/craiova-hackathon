"""DOCX / PDF export: structure, audience filtering, embedded evidence, API."""

from __future__ import annotations

import io
import struct
import zlib

import httpx
import pytest
from docx import Document

from app.evidence import capture
from app.main import create_app
from app.report import build_blocks
from app.report.export import filename, to_docx, to_pdf
from connectors.base import EntityType


def tiny_png() -> bytes:
    """A valid 1x1 PNG (python-docx reads the image header)."""
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b""))


async def _report(platform, make_investigation, settings):
    inv = make_investigation()
    seed = platform.resolver.ensure_entity(inv["id"], EntityType.DOMAIN, "example.com")
    await platform.ledger.run(inv, "dns", "example.com", user="t", run_by="t", entity_id=seed)
    dns_f = platform.db.one("SELECT id FROM findings WHERE connector = 'dns'")["id"]
    shot = await capture(platform.ledger, inv, "https://news.example/story", user="t",
                         run_by="t")
    for png in (settings.evidence_dir / inv["id"] / "screenshots").glob("*.png"):
        png.write_bytes(tiny_png())
    body = {"title": "Example SRL: footprint", "summary": "Short summary.",
            "sections": [{"title": "Infrastructure", "claims": [
                {"id": "c1", "text": "example.com resolves to 203.0.113.10.",
                 "evidence": [dns_f], "status": "verified", "severity": "low"},
                {"id": "c2", "text": "A news story mentions the company.",
                 "evidence": [shot.finding_id], "status": "unverified"}]}],
            "outstanding": ["Confirm the registrant with the registry."],
            "method": "Passive collection only."}
    return inv, body


def _docx_text(data: bytes) -> tuple[Document, str]:
    doc = Document(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs]
    for t in doc.tables:
        for row in t.rows:
            parts += [c.text for c in row.cells]
    return doc, "\n".join(parts)


async def test_docx_internal_has_house_layout_and_evidence(platform, make_investigation, settings):
    inv, body = await _report(platform, make_investigation, settings)
    blocks = build_blocks(platform.db, inv, body, "internal", True, settings.evidence_dir)
    doc, text = _docx_text(to_docx(blocks))
    assert doc.sections[0].header.paragraphs[0].text == "- CONFIDENTIAL -"
    assert "confidential" in doc.sections[0].footer.paragraphs[0].text.lower()
    assert round(doc.sections[0].page_width.cm, 1) == 21.0     # A4
    assert "Example SRL: footprint" in text and "Infrastructure" in text
    assert "A news story mentions the company. (unverified)" in text
    assert "[LOW]" in text and "[1]" in text and "[2]" in text
    assert "Collection method (internal)" in text and "sha256" in text
    assert len(doc.inline_shapes) == 1                           # the screenshot


async def test_docx_client_omits_internal_detail(platform, make_investigation, settings):
    inv, body = await _report(platform, make_investigation, settings)
    blocks = build_blocks(platform.db, inv, body, "client", True, settings.evidence_dir)
    _doc, text = _docx_text(to_docx(blocks))
    for leak in ("sha256", "Collection method", inv["id"], "[LOW]"):
        assert leak not in text
    assert "(unverified)" in text and "Evidence" in text


async def test_docx_header_footer_configurable(platform, make_investigation, settings, monkeypatch):
    monkeypatch.setenv("OSINT_REPORT_HEADER", "RESTRICTED")
    monkeypatch.setenv("OSINT_REPORT_FOOTER", "Prepared for ACME only.")
    inv, body = await _report(platform, make_investigation, settings)
    doc, _ = _docx_text(to_docx(build_blocks(platform.db, inv, body, "client")))
    assert doc.sections[0].header.paragraphs[0].text == "RESTRICTED"
    assert doc.sections[0].footer.paragraphs[0].text == "Prepared for ACME only."


def test_filename():
    assert filename("Example SRL: footprint!", "client", "2026-10-30", "docx") == \
        "example-srl-footprint_client_2026-10-30.docx"


async def test_pdf_render():
    pytest.importorskip("playwright")
    try:
        data = await to_pdf("<!doctype html><html><head><title>T</title></head>"
                            "<body><h1>Report</h1><p>Text</p></body></html>")
    except Exception as e:  # Chromium not installed here
        pytest.skip(f"Chromium unavailable: {e}")
    assert data.startswith(b"%PDF")


@pytest.fixture
async def client(platform):
    app = create_app(platform)
    app.state.platform = platform
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test/api/v1") as c:
        yield c


async def test_api_render_docx_with_attachment_name(client, platform, make_investigation,
                                                   settings):
    inv, body = await _report(platform, make_investigation, settings)
    rid = (await client.post(f"/investigations/{inv['id']}/reports",
                             json={"body": body})).json()["id"]
    r = await client.get(f"/reports/{rid}/render", params={"format": "docx",
                                                           "audience": "client"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert 'filename="example-srl-footprint_client_' in r.headers["content-disposition"]
    assert r.content[:2] == b"PK"                                 # a zip (OOXML)
    assert (await client.get(f"/reports/{rid}/render",
                             params={"format": "odt"})).status_code == 422
    audit = platform.db.query("SELECT detail FROM audit WHERE action = 'report.render'")
    assert audit[-1]["detail"]["format"] == "docx"


async def test_api_docx_respects_lint_gate(client, platform, make_investigation, settings):
    inv, _body = await _report(platform, make_investigation, settings)
    bad = {"title": "Bad", "sections": [{"title": "S", "claims": [
        {"id": "c1", "text": "Unsourced claim."}]}]}
    rid = (await client.post(f"/investigations/{inv['id']}/reports",
                             json={"body": bad})).json()["id"]
    r = await client.get(f"/reports/{rid}/render", params={"format": "docx"})
    assert r.status_code == 409


# --- HTML page, source links, page captures, build id ------------------------------


async def test_html_audiences_differ_visibly(platform, make_investigation, settings):
    from app import report as rep
    inv, body = await _report(platform, make_investigation, settings)
    internal = rep.render(platform.db, inv, body, "internal", "html", True, settings.evidence_dir)
    client = rep.render(platform.db, inv, body, "client", "html", True, settings.evidence_dir)
    assert "Internal working copy" in internal and "Client copy" in client
    for leak in ("sha256", "SHA-256", "Collection method", inv["id"], "f_", "claim-c1",
                 "Low severity"):
        assert leak not in client
    assert 'id="claim-c1"' in internal and "Cited by" in internal and "Low severity" in internal
    # Every citation points at an evidence entry that exists.
    for page in (internal, client):
        assert 'href="#ev1"' in page and 'id="ev1"' in page and 'id="ev2"' in page
        assert "data:image/png;base64," in page and "What the source returned" in page


async def test_source_url_and_capturable(platform, make_investigation, settings):
    from app import report as rep
    inv, body = await _report(platform, make_investigation, settings)
    await platform.ledger.run(inv, "crtsh", "example.com", user="t", run_by="t")
    crt = platform.db.one("SELECT * FROM findings WHERE connector = 'crtsh'")
    dns = platform.db.one("SELECT * FROM findings WHERE connector = 'dns'")
    assert rep.source_url(platform.db, crt) == "https://crt.sh/?q=example.com"
    assert rep.source_url(platform.db, dns) is None          # DNS has no page to show
    body["sections"][0]["claims"].append(
        {"id": "c3", "text": "Certificates list two subdomains.", "evidence": [crt["id"]]})
    # The screenshot finding is itself a capture; DNS has no page; crt.sh does.
    assert rep.capturable(platform.db, body) == [(crt["id"], "https://crt.sh/?q=example.com")]


async def test_api_capture_sources_attaches_screenshots(client, platform, make_investigation,
                                                        settings):
    inv, body = await _report(platform, make_investigation, settings)
    await platform.ledger.run(inv, "crtsh", "example.com", user="t", run_by="t")
    crt = platform.db.one("SELECT id FROM findings WHERE connector = 'crtsh'")["id"]
    body["sections"][0]["claims"].append(
        {"id": "c3", "text": "Certificates list two subdomains.", "evidence": [crt]})
    rid = (await client.post(f"/investigations/{inv['id']}/reports",
                             json={"body": body})).json()["id"]
    pending = (await client.get(f"/reports/{rid}/captures")).json()["pending"]
    assert [x["finding"] for x in pending] == [crt]
    r = (await client.post(f"/reports/{rid}/captures")).json()
    assert [x["finding"] for x in r["captured"]] == [crt] and not r["failed"]
    stored = (await client.get(f"/reports/{rid}")).json()["body"]
    assert stored["captures"][crt] == r["captured"][0]["capture"]
    assert (await client.get(f"/reports/{rid}/captures")).json()["pending"] == []
    page = (await client.get(f"/reports/{rid}/render",
                             params={"format": "html", "audience": "client"})).text
    assert page.count("data:image/png;base64,") == 2          # news story + crt.sh page


async def test_health_reports_build_id(client, monkeypatch):
    from app import build
    build.build_id.cache_clear()
    monkeypatch.setenv("OSINT_BUILD", "0123456789abcdef")
    assert (await client.get("/health")).json() == {"ok": True, "build": "01234567"}
    build.build_id.cache_clear()
