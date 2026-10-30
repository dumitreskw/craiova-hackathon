"""Analyst-supplied material: upload files, paste notes, add links (app/materials.py)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .. import materials as mat
from ..models.schemas import MaterialLink, MaterialText
from ..platform import Platform
from .deps import current_user, get_platform, load_investigation

router = APIRouter(tags=["materials"])


def _load(p: Platform, material_id: str) -> dict:
    m = p.db.get("materials", material_id)
    if m is None:
        raise HTTPException(404, "material not found")
    load_investigation(p, m["investigation_id"])
    return m


@router.get("/investigations/{investigation_id}/materials")
async def list_materials(investigation_id: str, p: Platform = Depends(get_platform)):
    load_investigation(p, investigation_id)
    return [mat.public(m) for m in p.db.query(
        "SELECT * FROM materials WHERE investigation_id = ? ORDER BY created_at",
        (investigation_id,))]


@router.post("/investigations/{investigation_id}/materials", status_code=201)
async def upload_materials(investigation_id: str, files: list[UploadFile] = File(...),
                           p: Platform = Depends(get_platform),
                           user: str = Depends(current_user)):
    """Upload PDF / DOCX / Markdown / text files. Each is converted to markdown;
    files that fail are reported per file, the others are still stored."""
    inv = load_investigation(p, investigation_id)
    added, errors = [], []
    for upload in files:
        data = await upload.read(mat.MAX_UPLOAD_BYTES + 1)
        try:
            m, created = await mat.add_file(p.ledger, inv, upload.filename or "", data, user=user)
            added.append({**mat.public(m), "duplicate": not created})
        except mat.MaterialError as e:
            errors.append({"name": upload.filename, "status": e.status, "error": str(e)})
    if not added and errors:
        raise HTTPException(errors[0]["status"] if len(errors) == 1 else 422,
                            "; ".join(e["error"] for e in errors))
    return {"materials": added, "errors": errors}


@router.post("/investigations/{investigation_id}/materials/text", status_code=201)
async def add_text(investigation_id: str, body: MaterialText,
                   p: Platform = Depends(get_platform), user: str = Depends(current_user)):
    inv = load_investigation(p, investigation_id)
    try:
        m, created = await mat.add_text(p.ledger, inv, body.title, body.text, user=user)
    except mat.MaterialError as e:
        raise HTTPException(e.status, str(e)) from e
    return {**mat.public(m), "duplicate": not created}


@router.post("/investigations/{investigation_id}/materials/link", status_code=201)
async def add_link(investigation_id: str, body: MaterialLink,
                   p: Platform = Depends(get_platform), user: str = Depends(current_user)):
    """Read a page now and keep it as material (target-host rule applies)."""
    inv = load_investigation(p, investigation_id)
    try:
        m, created = await mat.add_link(p.ledger, inv, body.url, user=user)
    except mat.MaterialError as e:
        raise HTTPException(e.status, str(e)) from e
    return {**mat.public(m), "duplicate": not created}


@router.get("/materials/{material_id}")
async def get_material(material_id: str, p: Platform = Depends(get_platform)):
    """The material with its converted markdown."""
    m = _load(p, material_id)
    return {**mat.public(m), "markdown": mat.text_of(p.ledger, m)}


@router.get("/materials/{material_id}/file")
async def material_file(material_id: str, p: Platform = Depends(get_platform)):
    m = _load(p, material_id)
    path = mat.material_dir(p.ledger, m["investigation_id"]) / (m["file"] or "")
    if not m["file"] or not path.is_file():
        raise HTTPException(404, "no original file for this material")
    return FileResponse(path, media_type=mat.MIME.get(m["format"], "application/octet-stream"),
                        filename=m["name"])


@router.delete("/materials/{material_id}")
async def withdraw_material(material_id: str, p: Platform = Depends(get_platform),
                            user: str = Depends(current_user)):
    m = _load(p, material_id)
    try:
        removed = mat.withdraw(p.ledger, m, user=user)
    except mat.MaterialError as e:
        raise HTTPException(e.status, str(e)) from e
    return {"withdrawn": material_id, "entities_removed": len(removed)}
