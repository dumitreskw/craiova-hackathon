"""Connector health, audit log, retention."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Query

import connectors

from ..platform import Platform
from ..retention import purge_expired
from .deps import current_user, get_platform

router = APIRouter(tags=["admin"])


@router.get("/health")
async def health():
    return {"ok": True}


@router.get("/connectors")
async def list_connectors():
    return [{
        "name": s.name, "accepts": s.accepts.value, "passive": s.passive,
        "description": s.description, "requires_key": s.requires_key,
        "key_configured": bool(os.environ.get(s.requires_key)) if s.requires_key else None,
    } for s in connectors.REGISTRY.values()]


@router.get("/audit")
async def audit(investigation_id: str | None = None, user: str | None = None,
                since: str | None = Query(None, alias="from"),
                until: str | None = Query(None, alias="to"),
                limit: int = Query(500, ge=1, le=5000), p: Platform = Depends(get_platform)):
    sql, params = "SELECT * FROM audit WHERE 1=1", []
    for col, op, val in (("investigation_id", "=", investigation_id), ("user", "=", user),
                         ("ts", ">=", since), ("ts", "<=", until)):
        if val:
            sql += f" AND {col} {op} ?"
            params.append(val)
    return p.db.query(sql + " ORDER BY id DESC LIMIT ?", [*params, limit])


@router.post("/admin/purge-expired")
async def purge(p: Platform = Depends(get_platform), user: str = Depends(current_user)):
    return {"purged": purge_expired(p.db, p.settings, user)}
