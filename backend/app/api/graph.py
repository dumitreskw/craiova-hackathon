"""Graph and entity endpoints: view, confirm/reject, merge, notes, pivot."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..models import new_id, now
from ..models.schemas import MergeRequest, NoteCreate, PivotRequest, StatusChange
from ..platform import Platform
from ..resolver import edge_view, entity_view
from .deps import current_user, get_platform, load_entity, load_investigation

router = APIRouter(tags=["graph"])


@router.get("/investigations/{investigation_id}/graph")
async def graph(investigation_id: str, min_confidence: float = Query(0.0, ge=0, le=1),
                types: str | None = None, include_candidates: bool = True,
                include_rejected: bool = False, p: Platform = Depends(get_platform)):
    """Cytoscape-compatible ``{nodes, edges}``."""
    load_investigation(p, investigation_id)
    statuses = ["active", "confirmed"]
    if include_candidates:
        statuses.append("candidate")
    if include_rejected:
        statuses.append("rejected")
    sql = (f"SELECT * FROM entities WHERE investigation_id = ? AND confidence >= ? "
           f"AND status IN ({','.join('?' * len(statuses))})")
    params: list = [investigation_id, min_confidence, *statuses]
    if types:
        wanted = [t.strip() for t in types.split(",") if t.strip()]
        sql += f" AND type IN ({','.join('?' * len(wanted))})"
        params += wanted
    nodes = [entity_view(r) for r in p.db.query(sql, params)]
    ids = {n["id"] for n in nodes}
    edges = [edge_view(r) for r in p.db.query(
        "SELECT * FROM edges WHERE investigation_id = ?", (investigation_id,))
        if r["from_id"] in ids and r["to_id"] in ids]
    return {"nodes": [{"data": n} for n in nodes], "edges": [{"data": e} for e in edges]}


@router.get("/entities/{entity_id}")
async def get_entity(entity_id: str, p: Platform = Depends(get_platform)):
    row = load_entity(p, entity_id)
    sources = p.db.query(
        "SELECT f.id, f.connector, f.input, f.fetched_at, f.sha256, es.reliability, "
        "es.match_quality FROM entity_sources es JOIN findings f ON f.id = es.finding_id "
        "WHERE es.entity_id = ? ORDER BY f.fetched_at", (entity_id,))
    edges = [edge_view(r) for r in p.db.query(
        "SELECT * FROM edges WHERE from_id = ? OR to_id = ?", (entity_id, entity_id))]
    notes = p.db.query("SELECT * FROM notes WHERE entity_id = ? ORDER BY created_at",
                       (entity_id,))
    return {**entity_view(row), "attribute_sources": row["attributes"], "sources": sources,
            "edges": edges, "notes": notes}


@router.get("/entities/{entity_id}/evidence")
async def entity_evidence(entity_id: str, p: Platform = Depends(get_platform)):
    load_entity(p, entity_id)
    return p.db.query(
        "SELECT f.* FROM entity_sources es JOIN findings f ON f.id = es.finding_id "
        "WHERE es.entity_id = ? ORDER BY f.fetched_at", (entity_id,))


async def _set_status(p: Platform, entity_id: str, status: str, reason: str, user: str):
    row = load_entity(p, entity_id)
    p.resolver.set_status(entity_id, status)
    p.db.audit(user=user, action=f"entity.{status}", investigation_id=row["investigation_id"],
               target=row["key"], detail={"entity_id": entity_id, "reason": reason,
                                          "previous_status": row["status"]})
    p.bus.publish(row["investigation_id"], "entity.updated",
                  {"entity": entity_view(p.db.get("entities", entity_id))})
    return entity_view(p.db.get("entities", entity_id))


@router.post("/entities/{entity_id}/confirm")
async def confirm(entity_id: str, body: StatusChange, p: Platform = Depends(get_platform),
                  user: str = Depends(current_user)):
    """Analyst confirms the entity (confidence 1.0). For a legal candidate, the
    reason should name the disambiguator (DOB, nationality, photo, ID)."""
    return await _set_status(p, entity_id, "confirmed", body.reason, user)


@router.post("/entities/{entity_id}/reject")
async def reject(entity_id: str, body: StatusChange, p: Platform = Depends(get_platform),
                 user: str = Depends(current_user)):
    return await _set_status(p, entity_id, "rejected", body.reason, user)


@router.post("/entities/merge")
async def merge(body: MergeRequest, p: Platform = Depends(get_platform),
                user: str = Depends(current_user)):
    keep = load_entity(p, body.ids[0])
    try:
        p.resolver.merge(body.ids[0], body.ids[1:])
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    p.db.audit(user=user, action="entity.merge", investigation_id=keep["investigation_id"],
               detail={"kept": body.ids[0], "merged": body.ids[1:]})
    return entity_view(p.db.get("entities", body.ids[0]))


@router.post("/entities/{entity_id}/notes", status_code=201)
async def add_note(entity_id: str, body: NoteCreate, p: Platform = Depends(get_platform),
                   user: str = Depends(current_user)):
    load_entity(p, entity_id)
    note = {"id": new_id("n"), "entity_id": entity_id, "author": user, "body": body.body,
            "created_at": now()}
    p.db.insert("notes", note)
    return note


@router.post("/entities/{entity_id}/pivot", status_code=202)
async def pivot(entity_id: str, body: PivotRequest, p: Platform = Depends(get_platform),
                user: str = Depends(current_user)):
    """Run connectors on one entity. An explicit analyst pivot treats the entity
    as a seed, so person screening (which a scan never auto-runs on discovered
    people) is available here, and audited."""
    row = load_entity(p, entity_id)
    if row["status"] in ("candidate", "rejected"):
        raise HTTPException(409, f"cannot pivot on a {row['status']} entity")
    inv = load_investigation(p, row["investigation_id"])
    p.db.audit(user=user, action="entity.pivot", investigation_id=inv["id"], target=row["key"],
               detail={"entity_id": entity_id, "connectors": body.connectors,
                       "depth": body.depth})
    run_id = p.scan.start(inv, [entity_id], user, max_depth=body.depth - 1,
                          only=body.connectors, kind="pivot")
    return {"run_id": run_id}
