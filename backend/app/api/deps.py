from __future__ import annotations

from fastapi import HTTPException, Request

from ..auth.context import Principal, current_principal
from ..platform import Platform


def get_platform(request: Request) -> Platform:
    return request.app.state.platform


def get_principal() -> Principal:
    """The signed-in caller (the auth middleware guarantees one on /api)."""
    p = current_principal()
    if p is None:
        raise HTTPException(401, "Not signed in.")
    return p


def current_user() -> str:
    """Who is acting, for the audit log: the signed-in account, never a client header."""
    return get_principal().label


def require_org() -> Principal:
    """A caller acting inside a workspace."""
    p = get_principal()
    if not p.org_id:
        raise HTTPException(409, "You are not in a workspace yet; create one or accept an "
                                 "invitation.")
    return p


def require_role(minimum: str):
    def dep() -> Principal:
        p = require_org()
        if not p.can(minimum):
            raise HTTPException(403, f"This needs the {minimum} role in the workspace.")
        return p
    return dep


def require_platform_admin() -> Principal:
    p = get_principal()
    if not p.platform_admin:
        raise HTTPException(403, "Platform administrators only.")
    return p


def load_investigation(p: Platform, investigation_id: str) -> dict:
    """An investigation of the caller's workspace; anything else is a 404 (no
    hint that another tenant's id exists)."""
    inv = p.db.get("investigations", investigation_id)
    who = current_principal()
    if (inv is None or inv["deleted_at"] or who is None or not who.org_id
            or inv.get("org_id") != who.org_id):
        raise HTTPException(404, "investigation not found")
    return inv


def load_entity(p: Platform, entity_id: str) -> dict:
    ent = p.db.get("entities", entity_id)
    if ent is None:
        raise HTTPException(404, "entity not found")
    load_investigation(p, ent["investigation_id"])
    return ent
