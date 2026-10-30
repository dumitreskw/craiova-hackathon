"""Reports: create (from the graph or a supplied body), edit, lint, render."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from .. import report as rep
from ..models import new_id, now
from ..models.schemas import Audience, ReportCreate, ReportUpdate
from ..platform import Platform
from ..report import export
from .deps import current_user, get_platform, load_investigation

router = APIRouter(tags=["reports"])

FORMATS = {
    "md": "text/markdown",
    "html": "text/html",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}


def _load(p: Platform, report_id: str) -> tuple[dict, dict]:
    r = p.db.get("reports", report_id)
    if r is None:
        raise HTTPException(404, "report not found")
    return r, load_investigation(p, r["investigation_id"])


@router.post("/investigations/{investigation_id}/reports", status_code=201)
async def create_report(investigation_id: str, body: ReportCreate,
                        p: Platform = Depends(get_platform), user: str = Depends(current_user)):
    inv = load_investigation(p, investigation_id)
    content = body.body or rep.draft_from_graph(p.db, inv, body.min_confidence)
    if body.title:
        content["title"] = body.title
    report_id = new_id("rep")
    ts = now()
    p.db.insert("reports", {"id": report_id, "investigation_id": investigation_id,
                            "title": content.get("title") or inv["name"], "body": content,
                            "created_by": user, "created_at": ts, "updated_at": ts})
    p.db.audit(user=user, action="report.create", investigation_id=investigation_id,
               detail={"report_id": report_id, "drafted": body.body is None})
    return {**p.db.get("reports", report_id),
            "lint": rep.lint(p.db, investigation_id, content)}


@router.get("/investigations/{investigation_id}/reports")
async def list_reports(investigation_id: str, p: Platform = Depends(get_platform)):
    load_investigation(p, investigation_id)
    return p.db.query("SELECT id, title, created_by, created_at, updated_at FROM reports "
                      "WHERE investigation_id = ? ORDER BY updated_at DESC", (investigation_id,))


@router.get("/reports/{report_id}")
async def get_report(report_id: str, p: Platform = Depends(get_platform)):
    r, _inv = _load(p, report_id)
    return r


@router.put("/reports/{report_id}")
async def update_report(report_id: str, body: ReportUpdate, p: Platform = Depends(get_platform),
                        user: str = Depends(current_user)):
    r, inv = _load(p, report_id)
    p.db.update("reports", report_id, {"body": body.body, "updated_at": now(),
                                       "title": body.body.get("title") or r["title"]})
    p.db.audit(user=user, action="report.update", investigation_id=inv["id"],
               detail={"report_id": report_id})
    return {**p.db.get("reports", report_id), "lint": rep.lint(p.db, inv["id"], body.body)}


@router.get("/reports/{report_id}/lint")
async def lint_report(report_id: str, audience: Audience = "internal",
                      p: Platform = Depends(get_platform)):
    r, inv = _load(p, report_id)
    issues = rep.lint(p.db, inv["id"], r["body"], audience)
    return {"ok": not any(i["level"] == "error" for i in issues), "issues": issues}


@router.get("/reports/{report_id}/render")
async def render_report(report_id: str, audience: Audience = "internal",
                        format: str = "md", cite: bool = True, force: bool = False,
                        p: Platform = Depends(get_platform), user: str = Depends(current_user)):
    """Render for an audience. Lint errors block the render unless ``force``,
    and a forced render is audited."""
    if format not in FORMATS:
        raise HTTPException(422, f"format must be one of {', '.join(FORMATS)}")
    r, inv = _load(p, report_id)
    errors = [i for i in rep.lint(p.db, inv["id"], r["body"], audience) if i["level"] == "error"]
    if errors and not force:
        raise HTTPException(409, {"message": "report has lint errors", "issues": errors})
    p.db.audit(user=user, action="report.render", investigation_id=inv["id"],
               outcome="ok", detail={"report_id": report_id, "audience": audience,
                                     "format": format,
                                     "forced_past_errors": len(errors) if force else 0})
    title = r["body"].get("title") or r["title"]
    name = export.filename(title, audience, now()[:10], format)
    attachment = {"Content-Disposition": f'attachment; filename="{name}"'}
    if format == "docx":
        blocks = rep.build_blocks(p.db, inv, r["body"], audience=audience, cite=cite,
                                  evidence_dir=p.settings.evidence_dir)
        data = await asyncio.to_thread(export.to_docx, blocks)
        return Response(data, media_type=FORMATS["docx"], headers=attachment)
    out = rep.render(p.db, inv, r["body"], audience=audience,
                     fmt="md" if format == "md" else "html", cite=cite,
                     evidence_dir=p.settings.evidence_dir)
    if format == "pdf":
        try:
            data = await export.to_pdf(out)
        except Exception as e:  # Chromium missing or failed to start
            raise HTTPException(503, f"PDF export needs Playwright Chromium: {e}") from e
        return Response(data, media_type=FORMATS["pdf"], headers=attachment)
    if format == "html":
        return HTMLResponse(out)
    return PlainTextResponse(out, media_type="text/markdown")
