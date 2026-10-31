"""Investigations, targets, scans, findings and the live event stream."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..detect import DetectError, detect
from ..events import sse_format
from ..models import new_id, now
from ..models.schemas import (
    InvestigationCreate,
    InvestigationUpdate,
    ScanRequest,
    TargetCreate,
)
from ..platform import Platform
from ..resolver import entity_view
from ..retention import purge_investigation
from ..auth.context import Principal
from ..auth.middleware import get_auth
from ..auth.store import TenancyError
from .deps import current_user, get_platform, load_investigation, require_org

router = APIRouter(tags=["investigations"])


def _stats(p: Platform, inv_id: str) -> dict:
    count = lambda table: p.db.one(
        f"SELECT count(*) AS n FROM {table} WHERE investigation_id = ?", (inv_id,))["n"]
    return {"entities": count("entities"), "edges": count("edges"),
            "findings": count("findings"), "targets": count("targets")}


def _public(inv: dict) -> dict:
    return {k: v for k, v in inv.items() if k != "deleted_at"}


@router.post("/investigations", status_code=201)
async def create_investigation(body: InvestigationCreate, request: Request,
                               p: Platform = Depends(get_platform),
                               user: str = Depends(current_user),
                               who: Principal = Depends(require_org)):
    try:
        get_auth(request.app).store.check_limit(who.org_id, "investigations")
    except TenancyError as e:
        raise HTTPException(e.status, str(e)) from e
    inv = {
        "id": new_id("inv"), "name": body.name, "purpose": body.purpose,
        "mode": body.mode,
        "settings": body.settings.model_dump(),
        "retention_days": body.retention_days or p.settings.retention_days,
        "legal_hold": 0, "created_by": user, "created_at": now(), "org_id": who.org_id,
    }
    p.db.insert("investigations", inv)
    p.db.audit(user=user, action="investigation.create", investigation_id=inv["id"],
               detail={"purpose": body.purpose, "mode": body.mode})
    return _public(load_investigation(p, inv["id"]))


@router.get("/investigations")
async def list_investigations(p: Platform = Depends(get_platform),
                              who: Principal = Depends(require_org)):
    rows = p.db.query("SELECT * FROM investigations WHERE deleted_at IS NULL AND org_id = ? "
                      "ORDER BY created_at DESC", (who.org_id,))
    return [{**_public(r), "stats": _stats(p, r["id"])} for r in rows]


@router.get("/investigations/{investigation_id}")
async def get_investigation(investigation_id: str, p: Platform = Depends(get_platform)):
    inv = load_investigation(p, investigation_id)
    targets = p.db.query("SELECT * FROM targets WHERE investigation_id = ? ORDER BY created_at",
                         (investigation_id,))
    return {**_public(inv), "targets": targets, "stats": _stats(p, investigation_id)}


@router.patch("/investigations/{investigation_id}")
async def update_investigation(investigation_id: str, body: InvestigationUpdate,
                               p: Platform = Depends(get_platform),
                               user: str = Depends(current_user)):
    inv = load_investigation(p, investigation_id)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        return _public(inv)
    p.db.update("investigations", investigation_id, changes)
    # Audit the diff.
    p.db.audit(user=user, action="investigation.update", investigation_id=investigation_id,
               detail={k: {"from": inv.get(k), "to": v} for k, v in changes.items()})
    return _public(load_investigation(p, investigation_id))


@router.delete("/investigations/{investigation_id}", status_code=204)
async def delete_investigation(investigation_id: str, p: Platform = Depends(get_platform),
                               user: str = Depends(current_user)):
    inv = load_investigation(p, investigation_id)
    if inv["legal_hold"]:
        raise HTTPException(409, "investigation is on legal hold; release the hold first")
    purge_investigation(p.db, p.settings, investigation_id, user, "deleted by user")


# --- targets & scans ---------------------------------------------------------

@router.post("/investigations/{investigation_id}/targets", status_code=202)
async def add_target(investigation_id: str, body: TargetCreate,
                     p: Platform = Depends(get_platform), user: str = Depends(current_user)):
    inv = load_investigation(p, investigation_id)
    try:
        etype, key = detect(body.value, body.type)
    except DetectError as e:
        raise HTTPException(422, str(e)) from e
    entity_id = p.resolver.ensure_entity(investigation_id, etype, key)
    target_id = new_id("t")
    p.db.insert("targets", {"id": target_id, "investigation_id": investigation_id,
                            "value": body.value, "type": etype.value, "entity_id": entity_id,
                            "added_by": user, "created_at": now()})
    p.db.audit(user=user, action="target.add", investigation_id=investigation_id,
               target=key, detail={"type": etype.value})
    p.bus.publish(investigation_id, "entity.created",
                  {"entity": entity_view(p.db.get("entities", entity_id))})
    run_id = None
    if body.scan:
        run_id = p.scan.start(inv, [entity_id], user, max_depth=body.max_depth)
    return {"target_id": target_id, "entity_id": entity_id, "type": etype.value,
            "key": key, "run_id": run_id}


@router.post("/investigations/{investigation_id}/scan", status_code=202)
async def scan(investigation_id: str, body: ScanRequest, p: Platform = Depends(get_platform),
               user: str = Depends(current_user)):
    inv = load_investigation(p, investigation_id)
    ids = body.entity_ids or [t["entity_id"] for t in p.db.query(
        "SELECT entity_id FROM targets WHERE investigation_id = ?", (investigation_id,))]
    if not ids:
        raise HTTPException(422, "no targets to scan")
    run_id = p.scan.start(inv, ids, user, max_depth=body.max_depth, only=body.connectors)
    return {"run_id": run_id}


@router.get("/investigations/{investigation_id}/runs")
async def list_runs(investigation_id: str, p: Platform = Depends(get_platform)):
    load_investigation(p, investigation_id)
    return p.db.query("SELECT * FROM agent_runs WHERE investigation_id = ? "
                      "ORDER BY started_at DESC", (investigation_id,))


@router.get("/agent-runs/{run_id}")
async def get_run(run_id: str, p: Platform = Depends(get_platform)):
    run = p.db.get("agent_runs", run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    load_investigation(p, run["investigation_id"])
    return run


@router.post("/agent-runs/{run_id}/cancel")
async def cancel_run(run_id: str, p: Platform = Depends(get_platform),
                     user: str = Depends(current_user)):
    run = p.db.get("agent_runs", run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    load_investigation(p, run["investigation_id"])
    cancelled = p.scan.cancel(run_id) or p.agents.cancel(run_id)
    p.db.audit(user=user, action="run.cancel", investigation_id=run["investigation_id"],
               detail={"run_id": run_id, "was_running": cancelled})
    return {"cancelled": cancelled}


# --- findings (raw evidence) ---------------------------------------------------

@router.get("/investigations/{investigation_id}/findings")
async def list_findings(investigation_id: str, connector: str | None = None,
                        p: Platform = Depends(get_platform)):
    load_investigation(p, investigation_id)
    sql = "SELECT * FROM findings WHERE investigation_id = ?"
    params: list = [investigation_id]
    if connector:
        sql += " AND connector = ?"
        params.append(connector)
    return p.db.query(sql + " ORDER BY fetched_at", params)


@router.get("/findings/{finding_id}")
async def get_finding(finding_id: str, p: Platform = Depends(get_platform)):
    f = p.db.get("findings", finding_id)
    if f is None:
        raise HTTPException(404, "finding not found")
    load_investigation(p, f["investigation_id"])
    raw = None
    if f["raw_path"]:
        path = p.settings.data_dir / f["raw_path"]
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
    return {**f, "payload": raw}


# --- live updates ----------------------------------------------------------------

@router.get("/investigations/{investigation_id}/stream")
async def stream(investigation_id: str, request: Request, p: Platform = Depends(get_platform)):
    load_investigation(p, investigation_id)

    async def events():
        yield sse_format("hello", {"investigation_id": investigation_id})
        async for event, data in p.bus.subscribe(investigation_id):
            if await request.is_disconnected():
                break
            yield sse_format(event, data)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
