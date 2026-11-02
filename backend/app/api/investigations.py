"""Investigations, targets, scans, findings and the live event stream."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from connectors.base import ConnectorResult, EntityCandidate, EntityType, RelationshipCandidate

from .. import identity
from ..auth.context import Principal
from ..auth.middleware import get_auth
from ..auth.store import TenancyError
from ..detect import DetectError, canonical_key, detect
from ..events import sse_format
from ..models import new_id, now
from ..models.schemas import (
    IdentityChoice,
    InvestigationCreate,
    InvestigationUpdate,
    ProfileUpdate,
    ScanRequest,
    TargetCreate,
)
from ..platform import Platform
from ..resolver import entity_view
from ..retention import purge_investigation
from .deps import current_user, get_platform, load_investigation, require_org

router = APIRouter(tags=["investigations"])

# Searches that take the profile's context (name + employer or city): re-run
# when the profile gains one.
CONTEXT_SEARCHES = ["dork_search", "news"]


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

def _insert_target(p: Platform, inv_id: str, value: str, etype: EntityType, key: str,
                   user: str, profile: dict | None = None, via: str | None = None) -> dict:
    entity_id = p.resolver.ensure_entity(inv_id, etype, key)
    target = {"id": new_id("t"), "investigation_id": inv_id, "value": value,
              "type": etype.value, "entity_id": entity_id, "added_by": user,
              "created_at": now(), "profile": profile or {}}
    p.db.insert("targets", target)
    p.db.audit(user=user, action="target.add", investigation_id=inv_id, target=key,
               detail={"type": etype.value, **({"via": via} if via else {})})
    p.bus.publish(inv_id, "entity.created",
                  {"entity": entity_view(p.db.get("entities", entity_id))})
    return target


def _apply_profile(p: Platform, inv: dict, person: dict, profile: dict, user: str) -> list[str]:
    """Add the profile's known identifiers as targets of their own, tie them to
    the person through a ``subject_profile`` finding (the brief, cited like any
    source), and re-check screening hits against the profile. Returns the
    entity ids of the identifiers that are new targets."""
    inv_id = inv["id"]
    existing = {(t["type"], t["entity_id"]) for t in p.db.query(
        "SELECT type, entity_id FROM targets WHERE investigation_id = ?", (inv_id,))}
    added, ents, rels = [], [], []
    person_key = p.db.get("entities", person["entity_id"])["key"]
    edge = {"Email": "uses_email", "Username": "has_username", "Phone": "has_phone"}
    for value, etype_name in identity.identifiers(profile):
        etype = EntityType(etype_name)
        try:
            key = canonical_key(etype, value)
        except (DetectError, ValueError, UnicodeError):
            continue
        entity_id = p.resolver.ensure_entity(inv_id, etype, key)
        if (etype.value, entity_id) not in existing:
            _insert_target(p, inv_id, value, etype, key, user, via=f"profile of {person['value']}")
            existing.add((etype.value, entity_id))
            added.append(entity_id)
        ents.append(EntityCandidate(etype, key))
        rels.append(RelationshipCandidate(edge[etype.value], EntityType.PERSON, person_key,
                                          etype, key, {"basis": "subject profile"}))
    if rels:
        result = ConnectorResult(source=identity.PROFILE_SOURCE, passive=True,
                                 raw={"target": person["value"], "profile": profile},
                                 entities=[EntityCandidate(EntityType.PERSON, person_key), *ents],
                                 relationships=rels,
                                 note="The analyst's brief: who the subject is.")
        p.ledger.record(inv, identity.PROFILE_SOURCE, {"arg": person["value"]}, result,
                        user=user, run_by="analyst", entity_id=person["entity_id"])
    identity.screen_candidates(p.db, p.resolver, p.bus, inv_id, user=user)
    return added


@router.post("/investigations/{investigation_id}/targets", status_code=202)
async def add_target(investigation_id: str, body: TargetCreate,
                     p: Platform = Depends(get_platform), user: str = Depends(current_user)):
    inv = load_investigation(p, investigation_id)
    try:
        etype, key = detect(body.value, body.type)
    except DetectError as e:
        raise HTTPException(422, str(e)) from e
    profile = identity.clean_profile(body.profile.model_dump()) if body.profile else {}
    if profile and etype != EntityType.PERSON:
        raise HTTPException(422, "a subject profile is for Person targets only")
    target = _insert_target(p, investigation_id, body.value, etype, key, user, profile)
    entity_id = target["entity_id"]
    extra = _apply_profile(p, inv, target, profile, user) if profile else []
    run_id = None
    if body.scan:
        run_id = p.scan.start(inv, [entity_id, *extra], user, max_depth=body.max_depth)
    return {"target_id": target["id"], "entity_id": entity_id, "type": etype.value,
            "key": key, "run_id": run_id, "identifier_entity_ids": extra}


@router.put("/investigations/{investigation_id}/targets/{target_id}/profile")
async def set_profile(investigation_id: str, target_id: str, body: ProfileUpdate,
                      p: Platform = Depends(get_platform), user: str = Depends(current_user)):
    """Replace a Person target's subject profile. New identifiers become targets
    (and are scanned); a new employer or city re-runs the name search with it."""
    inv = load_investigation(p, investigation_id)
    target = p.db.get("targets", target_id)
    if target is None or target["investigation_id"] != investigation_id:
        raise HTTPException(404, "target not found")
    if target["type"] != EntityType.PERSON.value:
        raise HTTPException(422, "a subject profile is for Person targets only")
    before = target.get("profile") or {}
    profile = identity.clean_profile(body.profile.model_dump())
    p.db.update("targets", target_id, {"profile": profile})
    p.db.audit(user=user, action="target.profile", investigation_id=investigation_id,
               target=target["value"], detail={"profile": profile})
    added = _apply_profile(p, inv, {**target, "profile": profile}, profile, user)
    runs = []
    if body.scan and added:
        runs.append(p.scan.start(inv, added, user))
    value = target["value"]
    if body.scan and (identity.search_query(value, profile)
                      != identity.search_query(value, before)):
        runs.append(p.scan.start(inv, [target["entity_id"]], user, max_depth=0,
                                 only=CONTEXT_SEARCHES, kind="pivot"))
    return {"target": p.db.get("targets", target_id), "identifier_entity_ids": added,
            "run_ids": runs, **identity.overview(p.db, investigation_id)}


@router.get("/investigations/{investigation_id}/identity")
async def get_identity(investigation_id: str, p: Platform = Depends(get_platform)):
    """Who is the subject: profiles, the common-name warning, and the who's-who."""
    load_investigation(p, investigation_id)
    return identity.overview(p.db, investigation_id)


@router.post("/identity-clusters/{cluster_id}/choose")
async def choose_identity(cluster_id: str, body: IdentityChoice,
                          p: Platform = Depends(get_platform),
                          user: str = Depends(current_user)):
    """The analyst picks which same-name person is the subject."""
    row = p.db.get("identity_clusters", cluster_id)
    if row is None:
        raise HTTPException(404, "identity not found")
    load_investigation(p, row["investigation_id"])
    try:
        out = identity.choose(p.db, p.resolver, p.bus, cluster_id, body.reason, user)
    except identity.IdentityError as e:
        raise HTTPException(422, str(e)) from e
    # Search again with what is now known about the subject.
    inv = load_investigation(p, row["investigation_id"])
    out["run_ids"] = [p.scan.start(inv, [eid], user, max_depth=0, only=CONTEXT_SEARCHES,
                                   kind="pivot") for eid in out.pop("refresh", [])]
    return out


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
