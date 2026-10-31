"""Claude agents (collect / report) and evidence capture."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from ..agent import ensure_target
from ..evidence import capture, read_page, screenshot_dir
from .. import review
from ..models.schemas import (CaptureRequest, CollectRequest, DeepRequest, ReportAgentRequest,
                              VerifyRequest)
from ..platform import Platform
from ..resolver import entity_view
from .deps import current_user, get_platform, load_investigation

router = APIRouter(tags=["agents"])


def _require_model(p: Platform, kind: str, override: str | None) -> None:
    """The model assigned to this task (or the per-run override) must be usable."""
    if not p.agents.model_factory_is_default:  # a test/explicit model is injected
        return
    model_id = override or p.assigned_model(kind)
    err = p.model_error(model_id)
    if err:
        raise HTTPException(503, f"{err} The quick scan and the graph draft report work "
                                 "without a model, or assign a usable model on the Models page.")


@router.post("/investigations/{investigation_id}/collect", status_code=202)
async def collect(investigation_id: str, body: CollectRequest,
                  p: Platform = Depends(get_platform), user: str = Depends(current_user)):
    """Start the collection agent (docs/05-api.md)."""
    inv = load_investigation(p, investigation_id)
    _require_model(p, "collection", body.model)
    if body.target and ensure_target(p, inv, body.target, user) is None:
        raise HTTPException(422, f"could not detect the type of {body.target!r}")
    if not p.db.one("SELECT 1 AS x FROM targets WHERE investigation_id = ?", (investigation_id,)):
        raise HTTPException(422, "add a target first (or pass one as 'target')")
    run_id = p.agents.start(inv, "collection", body.goal, user, body.max_tool_calls,
                            body.costly_connectors, model=body.model)
    return {"run_id": run_id, "kind": "collection"}


@router.post("/investigations/{investigation_id}/deep", status_code=202)
async def deep(investigation_id: str, body: DeepRequest,
               p: Platform = Depends(get_platform), user: str = Depends(current_user)):
    """Deep analysis: an orchestrator dispatches parallel worker agents, then the
    report agent writes the report (docs/02-architecture.md, mode 4)."""
    inv = load_investigation(p, investigation_id)
    _require_model(p, "deep", body.model)
    if body.target and ensure_target(p, inv, body.target, user) is None:
        raise HTTPException(422, f"could not detect the type of {body.target!r}")
    if not p.db.one("SELECT 1 AS x FROM targets WHERE investigation_id = ?", (investigation_id,)):
        raise HTTPException(422, "add a target first (or pass one as 'target')")
    run_id = p.agents.start(inv, "deep", body.goal, user, body.max_tool_calls,
                            body.costly_connectors, model=body.model)
    return {"run_id": run_id, "kind": "deep"}


@router.post("/investigations/{investigation_id}/report-agent", status_code=202)
async def report_agent(investigation_id: str, body: ReportAgentRequest,
                       p: Platform = Depends(get_platform), user: str = Depends(current_user)):
    """Start the report pipeline: verification, the writer (one dossier, linted report),
    then an independent fact-check and one revision round."""
    inv = load_investigation(p, investigation_id)
    _require_model(p, "report", body.model)
    if not p.db.one("SELECT 1 AS x FROM findings WHERE investigation_id = ?", (investigation_id,)):
        raise HTTPException(422, "nothing collected yet; run a scan or the collection agent first")
    run_id = p.agents.start(inv, "report", body.goal, user, body.max_tool_calls,
                            verify=body.verify, review=body.review, model=body.model)
    return {"run_id": run_id, "kind": "report"}


@router.post("/investigations/{investigation_id}/verify", status_code=202)
async def verify(investigation_id: str, body: VerifyRequest,
                 p: Platform = Depends(get_platform), user: str = Depends(current_user)):
    """Start the verification agent: a verdict on every entity worth one."""
    inv = load_investigation(p, investigation_id)
    _require_model(p, "verify", body.model)
    run_id = p.agents.start(inv, "verify", body.goal, user, body.max_tool_calls, model=body.model)
    return {"run_id": run_id, "kind": "verify"}


@router.get("/investigations/{investigation_id}/reviews")
async def reviews(investigation_id: str, p: Platform = Depends(get_platform)):
    """Verdict counts, and the entities left uncertain for the analyst."""
    load_investigation(p, investigation_id)
    uncertain = p.db.query(
        "SELECT r.entity_id, r.reason, r.reviewer, r.by_agent, r.created_at, e.type, e.key "
        "FROM entity_reviews r JOIN entities e ON e.id = r.entity_id "
        "WHERE r.investigation_id = ? AND r.verdict = 'uncertain' AND r.rowid = "
        "(SELECT max(rowid) FROM entity_reviews x WHERE x.entity_id = r.entity_id) "
        "ORDER BY e.type, e.key", (investigation_id,))
    return {**review.summary(p.db, investigation_id), "uncertain_items": uncertain}


@router.post("/investigations/{investigation_id}/capture")
async def capture_evidence(investigation_id: str, body: CaptureRequest,
                           p: Platform = Depends(get_platform), user: str = Depends(current_user)):
    """Screenshot a URL now (takes a few seconds) and return the finding."""
    inv = load_investigation(p, investigation_id)
    if body.entity_id:
        ent = p.db.get("entities", body.entity_id)
        if ent is None or ent["investigation_id"] != investigation_id:
            raise HTTPException(404, "entity not found")
    outcome = await capture(p.ledger, inv, body.url, user=user, run_by="analyst",
                            entity_id=body.entity_id)
    if outcome.refused:
        raise HTTPException(403, outcome.error)
    if outcome.error:
        raise HTTPException(502, {"message": outcome.error, "finding_id": outcome.finding_id})
    docs = [e for e in outcome.fold.new_entities if e["type"] == "Document"]
    return {"finding": p.db.get("findings", outcome.finding_id),
            "raw": outcome.result.raw,
            "document": docs[0] if docs else None}


@router.post("/investigations/{investigation_id}/read")
async def read(investigation_id: str, body: CaptureRequest,
               p: Platform = Depends(get_platform), user: str = Depends(current_user)):
    """Read a page now (text, contacts, links, structured data) into the graph."""
    inv = load_investigation(p, investigation_id)
    outcome = await read_page(p.ledger, inv, body.url, user=user, run_by="analyst",
                              entity_id=body.entity_id)
    if outcome.refused:
        raise HTTPException(403, outcome.error)
    if outcome.error:
        raise HTTPException(502, {"message": outcome.error, "finding_id": outcome.finding_id})
    return {"finding": p.db.get("findings", outcome.finding_id), "raw": outcome.result.raw}


@router.get("/findings/{finding_id}/screenshot")
async def finding_screenshot(finding_id: str, p: Platform = Depends(get_platform)):
    f = p.db.get("findings", finding_id)
    if f is None or f["connector"] not in ("screenshot", "social_capture") or not f["raw_path"]:
        raise HTTPException(404, "no screenshot for this finding")
    load_investigation(p, f["investigation_id"])
    doc = p.db.one(
        "SELECT e.* FROM entity_sources es JOIN entities e ON e.id = es.entity_id "
        "WHERE es.finding_id = ? AND e.type = 'Document'", (finding_id,))
    name = entity_view(doc)["attributes"].get("file") if doc else None
    path = screenshot_dir(p.ledger, f["investigation_id"]) / (name or "")
    if not name or not path.is_file():
        raise HTTPException(404, "screenshot file missing")
    return FileResponse(path, media_type="image/png")
