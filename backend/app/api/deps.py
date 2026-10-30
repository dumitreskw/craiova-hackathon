from __future__ import annotations

from fastapi import Header, HTTPException, Request

from ..platform import Platform


def get_platform(request: Request) -> Platform:
    return request.app.state.platform


def current_user(x_analyst: str | None = Header(None)) -> str:
    """Who is acting, for the audit log.

    Caddy's basic auth sets ``X-Analyst`` from the authenticated user
    (``header_up X-Analyst {http.auth.user.id}``); local runs fall back to "analyst".
    """
    return (x_analyst or "analyst").strip()[:100]


def load_investigation(p: Platform, investigation_id: str) -> dict:
    inv = p.db.get("investigations", investigation_id)
    if inv is None or inv["deleted_at"]:
        raise HTTPException(404, "investigation not found")
    return inv


def load_entity(p: Platform, entity_id: str) -> dict:
    ent = p.db.get("entities", entity_id)
    if ent is None:
        raise HTTPException(404, "entity not found")
    load_investigation(p, ent["investigation_id"])
    return ent
