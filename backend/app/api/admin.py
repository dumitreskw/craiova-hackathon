"""Connector health, audit log, retention."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Query

import connectors
from connectors import _cli

from .. import credits as credit_check
from ..build import build_id
from ..platform import Platform
from ..retention import purge_expired
from ..auth.context import Principal
from .deps import current_user, get_platform, require_org, require_platform_admin

router = APIRouter(tags=["admin"])


@router.get("/health")
async def health():
    return {"ok": True, "build": build_id()}


# Connectors backed by a command-line tool (bundled in the image or via the Kali VM).
_CLI_TOOLS = {"subfinder": "subfinder", "dnstwist": "dnstwist", "holehe": "holehe",
              "maigret": "maigret"}


@router.get("/connectors")
async def list_connectors(p: Platform = Depends(get_platform)):
    out = []
    for s in connectors.REGISTRY.values():
        available = bool(os.environ.get(s.requires_key)) if s.requires_key else True
        if s.name in _CLI_TOOLS:
            available = _cli.available(_CLI_TOOLS[s.name])
        out.append({
            "name": s.name, "accepts": s.accepts.value, "passive": s.passive,
            "description": s.description, "requires_key": s.requires_key,
            "key_configured": bool(os.environ.get(s.requires_key)) if s.requires_key else None,
            "available": available, "cost": s.cost, "seed_only": s.seed_only,
            "quota": s.quota, "quota_days": s.quota_days if s.quota else None,
            "quota_used": p.ledger.quota_used(s.name, s.quota_days) if s.quota else None,
        })
    return out


@router.get("/credits")
async def credits(refresh: bool = False, p: Platform = Depends(get_platform),
                  _=Depends(require_platform_admin)):
    """Live credit / quota balance per paid or metered source (cached 5 min).

    The platform's own provider accounts: platform admins only."""
    return await credit_check.credits(p.db, refresh=refresh)


@router.get("/audit")
async def audit(investigation_id: str | None = None, user: str | None = None,
                since: str | None = Query(None, alias="from"),
                until: str | None = Query(None, alias="to"),
                limit: int = Query(500, ge=1, le=5000), p: Platform = Depends(get_platform),
                who: Principal = Depends(require_org)):
    # The caller's workspace only; rows from before accounts existed have no
    # org and are matched through their investigation.
    sql = ("SELECT * FROM audit WHERE (org_id = ? OR (org_id IS NULL AND investigation_id IN "
           "(SELECT id FROM investigations WHERE org_id = ?)))")
    params: list = [who.org_id, who.org_id]
    for col, op, val in (("investigation_id", "=", investigation_id), ("user", "=", user),
                         ("ts", ">=", since), ("ts", "<=", until)):
        if val:
            sql += f" AND {col} {op} ?"
            params.append(val)
    return p.db.query(sql + " ORDER BY id DESC LIMIT ?", [*params, limit])


@router.post("/admin/purge-expired")
async def purge(p: Platform = Depends(get_platform), user: str = Depends(current_user),
                _=Depends(require_platform_admin)):
    return {"purged": purge_expired(p.db, p.settings, user)}
