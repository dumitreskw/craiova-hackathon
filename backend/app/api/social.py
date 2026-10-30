"""Social capture through personas: the analyst side and the Kali worker side.

The worker endpoints (``/worker/...``) take the shared ``X-Worker-Token``
(``OSINT_WORKER_TOKEN``); everything else is the normal analyst API.
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field

from ..platform import Platform
from ..social import PLATFORMS, SocialError
from .deps import current_user, get_platform, load_investigation

router = APIRouter(tags=["social"])


class PersonaCreate(BaseModel):
    name: str = Field(min_length=2, max_length=40)
    platform: str
    region: str = Field(min_length=2, max_length=6,
                        description="Mullvad location the persona always uses: 'de' or 'de-fra'")
    note: str | None = Field(None, max_length=500)


class PersonaUpdate(BaseModel):
    region: str | None = Field(None, min_length=2, max_length=6)
    note: str | None = Field(None, max_length=500)
    disabled: bool | None = None


class SocialCapture(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    persona: str | None = Field(None, description="persona name, 'none' for logged-out only, "
                                                  "or empty to pick one at a login wall")
    entity_id: str | None = None


class WatchCreate(SocialCapture):
    interval_hours: int = Field(24, ge=1, le=24 * 30)


class WatchUpdate(BaseModel):
    active: bool | None = None
    interval_hours: int | None = Field(None, ge=1, le=24 * 30)


def worker_auth(p: Platform = Depends(get_platform),
                x_worker_token: str | None = Header(None)) -> Platform:
    token = p.settings.worker_token
    if not token:
        raise HTTPException(503, "worker API is off (set OSINT_WORKER_TOKEN)")
    if not x_worker_token or not hmac.compare_digest(x_worker_token, token):
        raise HTTPException(401, "bad worker token")
    return p


def _err(e: SocialError, status: int = 422) -> HTTPException:
    return HTTPException(status, str(e))

# --- personas ---------------------------------------------------------------

@router.get("/social/status")
async def social_status(p: Platform = Depends(get_platform)):
    """Worker presence, VPN/egress, and every persona's health."""
    return p.social.status()


@router.get("/social/platforms")
async def social_platforms():
    return {"platforms": PLATFORMS}


@router.post("/social/personas", status_code=201)
async def create_persona(body: PersonaCreate, p: Platform = Depends(get_platform),
                         user: str = Depends(current_user)):
    try:
        return p.social.create_persona(body.name, body.platform, body.region, body.note, user)
    except SocialError as e:
        raise _err(e) from e


@router.patch("/social/personas/{name}")
async def update_persona(name: str, body: PersonaUpdate, p: Platform = Depends(get_platform),
                         user: str = Depends(current_user)):
    try:
        return p.social.update_persona(name, body.model_dump(exclude_none=True), user)
    except SocialError as e:
        raise _err(e, 404 if "no persona" in str(e) else 422) from e


@router.post("/social/personas/{name}/login", status_code=202)
async def login_persona(name: str, p: Platform = Depends(get_platform),
                        user: str = Depends(current_user)):
    """Queue a manual login: the worker opens a visible browser on Kali for you."""
    try:
        return p.social.login(name, user)
    except SocialError as e:
        raise _err(e, 409) from e


# --- capture & watches ------------------------------------------------------

@router.post("/investigations/{investigation_id}/social/capture", status_code=201)
async def capture_profile(investigation_id: str, body: SocialCapture,
                          p: Platform = Depends(get_platform),
                          user: str = Depends(current_user)):
    inv = load_investigation(p, investigation_id)
    outcome = await p.social.capture(inv, body.url, user=user, run_by="analyst",
                                     persona=body.persona, entity_id=body.entity_id)
    if outcome.refused:
        raise HTTPException(409, outcome.error)
    if outcome.error:
        raise HTTPException(502, {"message": outcome.error, "finding_id": outcome.finding_id})
    finding = p.db.get("findings", outcome.finding_id) if outcome.finding_id else None
    return {"finding": finding,
            "raw": outcome.result.raw if outcome.result else None,
            "note": outcome.result.note if outcome.result else None}


@router.get("/investigations/{investigation_id}/social/jobs")
async def list_jobs(investigation_id: str, limit: int = Query(50, ge=1, le=200),
                    p: Platform = Depends(get_platform)):
    load_investigation(p, investigation_id)
    return p.social.jobs(limit=limit, investigation_id=investigation_id)


@router.get("/investigations/{investigation_id}/social/watches")
async def list_watches(investigation_id: str, p: Platform = Depends(get_platform)):
    load_investigation(p, investigation_id)
    return p.social.watches(investigation_id)


@router.post("/investigations/{investigation_id}/social/watches", status_code=201)
async def create_watch(investigation_id: str, body: WatchCreate,
                       p: Platform = Depends(get_platform), user: str = Depends(current_user)):
    inv = load_investigation(p, investigation_id)
    try:
        return p.social.add_watch(inv, body.url, interval_hours=body.interval_hours,
                                  persona=body.persona, entity_id=body.entity_id, user=user)
    except SocialError as e:
        raise _err(e) from e


@router.patch("/social/watches/{watch_id}")
async def update_watch(watch_id: str, body: WatchUpdate, p: Platform = Depends(get_platform),
                       user: str = Depends(current_user)):
    try:
        return p.social.update_watch(watch_id, body.model_dump(exclude_none=True), user)
    except SocialError as e:
        raise _err(e, 404 if "no such watch" in str(e) else 422) from e


@router.delete("/social/watches/{watch_id}", status_code=204)
async def delete_watch(watch_id: str, p: Platform = Depends(get_platform),
                       user: str = Depends(current_user)):
    try:
        p.social.delete_watch(watch_id, user)
    except SocialError as e:
        raise _err(e, 404) from e
    return Response(status_code=204)


# --- Kali worker API (X-Worker-Token) ---------------------------------------

@router.post("/worker/heartbeat")
async def worker_heartbeat(body: dict, p: Platform = Depends(worker_auth)):
    """The worker reports VPN/egress state and each persona's session health."""
    p.social.heartbeat(body if isinstance(body, dict) else {})
    personas = [{"name": r["name"], "platform": r["platform"], "region": r["region"],
                 "status": r["status"]} for r in p.social.personas()]
    return {"ok": True, "personas": personas}


@router.get("/worker/jobs/next")
async def worker_next_job(wait: float = Query(25.0, ge=0, le=55),
                          p: Platform = Depends(worker_auth)):
    """Long-poll for the next job. Returns ``{"job": null}`` when idle."""
    job = await p.social.next_job(wait=wait)
    return {"job": job}


@router.post("/worker/jobs/{job_id}/complete")
async def worker_complete(job_id: str, body: dict, p: Platform = Depends(worker_auth)):
    try:
        return p.social.complete(job_id, body if isinstance(body, dict) else {})
    except SocialError as e:
        raise HTTPException(409, str(e)) from e
