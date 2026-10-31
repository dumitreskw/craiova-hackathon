"""Which model runs each agent task (the Models settings page).

``GET /models`` returns the catalogue (built-in Claude models, plus the live
Venice list when a key is set), the current per-task assignment, and whether
each provider is usable. ``PUT /models/assignments`` saves the analyst's
choices. Assigning a Venice model is a deliberate per-task provider choice; a
refusal still ends the run — nothing here switches models automatically.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..agent.catalog import (anthropic_catalog, fetch_venice_models, is_anthropic,
                             TASKS, TASK_KEYS)
from ..agent.model import configured, venice_configured
from ..models.schemas import ModelAssignments
from ..platform import Platform
from .deps import current_user, get_platform

router = APIRouter(tags=["models"])


async def _catalog(p: Platform) -> list[dict]:
    """Selectable models: the Claude shortlist, plus Venice's live list. Each
    carries ``available`` (provider key present) and ``tools`` (can drive an
    agent). A model already assigned is always shown even if now unavailable."""
    models = anthropic_catalog()
    for m in models:
        m["available"] = configured()
    if p.settings.venice_api_key:
        try:
            venice = await fetch_venice_models(p.settings.venice_api_key,
                                               p.settings.venice_base_url)
            models.extend(venice)
        except Exception as e:  # noqa: BLE001 — the page still works without the live list
            models.append({"id": "__venice_error__", "label": "Venice list unavailable",
                           "provider": "venice", "note": str(e)[:200],
                           "tools": False, "available": False, "hidden": True})
    known = {m["id"] for m in models}
    for model_id in set(p.model_assignments().values()):
        if model_id not in known:
            models.append({"id": model_id, "label": model_id,
                           "provider": "anthropic" if is_anthropic(model_id) else "venice",
                           "note": "Currently assigned.", "tools": True,
                           "available": p.model_error(model_id) is None})
    return models


@router.get("/models")
async def list_models(p: Platform = Depends(get_platform)):
    return {
        "tasks": TASKS,
        "assignments": p.model_assignments(),
        "defaults": {k: p.default_model(k) for k in TASK_KEYS},
        "providers": {
            "anthropic": {"available": configured()},
            "venice": {"available": venice_configured(),
                       "configured_list": bool(p.settings.venice_api_key)},
        },
        "models": await _catalog(p),
    }


@router.put("/models/assignments")
async def set_assignments(body: ModelAssignments, p: Platform = Depends(get_platform),
                          user: str = Depends(current_user)):
    saved = dict(p.db.get_setting("model_assignments", {}) or {})
    for kind, model_id in body.assignments.items():
        if kind not in TASK_KEYS:
            raise HTTPException(422, f"unknown task {kind!r}")
        if not model_id:  # clear -> fall back to the built-in default
            saved.pop(kind, None)
            continue
        err = p.model_error(model_id)
        if err:
            raise HTTPException(422, f"{kind}: {err}")
        saved[kind] = model_id
    p.db.set_setting("model_assignments", saved, user)
    p.db.audit(user=user, action="models.assign", detail={"assignments": saved})
    return {"assignments": p.model_assignments()}
