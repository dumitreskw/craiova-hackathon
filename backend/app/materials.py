"""Material the analyst supplies about the target: files, pasted notes, links.

docs/02-architecture.md, "Analyst-supplied material". Everything ends up as
markdown in a finding (connector ``analyst_material``, or ``web_read`` for a
link), so it is stored, hashed, audited and cited exactly like a lookup:

* PDF and DOCX are converted with Microsoft's markitdown (only its PDF and
  DOCX converters are enabled; no plugins, no network, no LLM). Markdown and
  plain text are taken as they are.
* Links are read with the ``read_page`` path, under the same target-host rule.
* The original upload is kept under ``evidence/<investigation>/materials/``.

The agents never get the full text up front. Their brief lists each item with
a short digest (size, headings, identifiers found, the opening lines); the
orchestrator decides what to read with ``read_material`` and what to hand
each worker, so a 200-page PDF costs tokens only when it is actually needed.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import io
import json
import re
from pathlib import Path
from typing import Any

from connectors.base import ConnectorResult, EntityCandidate, EntityType, RelationshipCandidate
from connectors.web_read import _EMAIL, _IMAGE_EXT, _RO_CUI, _UK_NO

from .evidence import read_page
from .ledger import Ledger
from .models import new_id, now

CONNECTOR = "analyst_material"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_TEXT_CHARS = 200_000         # pasted notes
MAX_MARKDOWN = 400_000           # chars of converted text kept in the finding
CONVERT_TIMEOUT_S = 120
FORMATS = {".pdf": "pdf", ".docx": "docx", ".md": "md", ".markdown": "md", ".txt": "txt"}
MIME = {"pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "md": "text/markdown", "txt": "text/plain"}

_LINK = re.compile(r"https?://[^\s<>\"'()\[\]{}]+")
_HEADING = re.compile(r"^#{1,4}\s+(.+?)\s*#*$", re.MULTILINE)
_LEADERS = re.compile(r"(?:[ \t]*\.){4,}[ \t]*")
_BLANKS = re.compile(r"\n[ \t]*(?:\n[ \t]*){2,}")


class MaterialError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def material_dir(ledger: Ledger, investigation_id: str) -> Path:
    return ledger.settings.evidence_dir / investigation_id / "materials"


# --- conversion ------------------------------------------------------------------------

@functools.cache
def _markitdown():
    from markitdown import MarkItDown
    from markitdown.converters import DocxConverter, PdfConverter

    md = MarkItDown(enable_builtins=False, enable_plugins=False)
    md.register_converter(PdfConverter())
    md.register_converter(DocxConverter())
    return md


def to_markdown(data: bytes, fmt: str) -> str:
    if fmt in ("md", "txt"):
        return data.decode("utf-8-sig", errors="replace")
    from markitdown import StreamInfo

    result = _markitdown().convert_stream(
        io.BytesIO(data), stream_info=StreamInfo(extension=f".{fmt}", mimetype=MIME[fmt]))
    # Tokens the agents would pay for and learn nothing from: table-of-contents
    # dot leaders and runs of blank lines.
    text = _LEADERS.sub(" … ", result.markdown or "")
    return _BLANKS.sub("\n\n", text).strip()


def extract(markdown: str) -> dict[str, list[str]]:
    """Identifiers worth pivoting on, for the digest and the graph."""
    emails = sorted({e.lower() for e in _EMAIL.findall(markdown)
                     if not e.lower().endswith(_IMAGE_EXT)})
    companies = sorted({f"RO{m.group(2)}" for m in _RO_CUI.finditer(markdown)}
                       | {f"GB{m.group(1).upper()}" for m in _UK_NO.finditer(markdown)})
    links = sorted({u.rstrip(".,;:") for u in _LINK.findall(markdown)})
    return {"emails": emails[:50], "company_identifiers": companies[:10], "urls": links[:30]}


def digest(markdown: str, found: dict[str, list[str]]) -> dict[str, Any]:
    """What an agent sees before deciding to read the material."""
    lead = re.sub(r"\s+", " ", markdown).strip()
    return {"outline": [h.strip()[:120] for h in _HEADING.findall(markdown)][:15],
            "preview": lead[:300], **found}


# --- ingest --------------------------------------------------------------------------

def _duplicate(ledger: Ledger, inv_id: str, column: str, value: str) -> dict | None:
    return ledger.db.one(f"SELECT * FROM materials WHERE investigation_id = ? AND {column} = ?",
                         (inv_id, value))


async def add_file(ledger: Ledger, investigation: dict, filename: str, data: bytes, *,
                   user: str) -> tuple[dict, bool]:
    """Store and convert one upload. Returns ``(material, created)``."""
    name = Path(filename or "upload").name[:200] or "upload"
    fmt = FORMATS.get(Path(name).suffix.lower())
    if fmt is None:
        raise MaterialError(415, f"{name}: supported files are PDF, DOCX, Markdown and text")
    if not data:
        raise MaterialError(422, f"{name} is empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise MaterialError(413, f"{name} is over {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    sha = hashlib.sha256(data).hexdigest()
    if existing := _duplicate(ledger, investigation["id"], "sha256", sha):
        return existing, False
    try:
        markdown = await asyncio.wait_for(asyncio.to_thread(to_markdown, data, fmt),
                                          CONVERT_TIMEOUT_S)
    except TimeoutError as e:
        raise MaterialError(422, f"{name}: conversion took too long") from e
    except Exception as e:  # corrupt or encrypted file: tell the analyst, store nothing
        raise MaterialError(422, f"{name} could not be converted: {type(e).__name__}: {e}"[:300]) from e
    out = material_dir(ledger, investigation["id"])
    out.mkdir(parents=True, exist_ok=True)
    stored = f"{sha[:20]}{Path(name).suffix.lower()}"
    (out / stored).write_bytes(data)
    note = None
    if not markdown.strip():
        note = ("no text could be extracted (a scanned PDF?); OCR is not run, so read the "
                "original" if fmt == "pdf" else "the file has no text")
    material = await _record(ledger, investigation, "file", name, fmt, len(data), sha, markdown,
                             user=user, file=stored, note=note)
    return material, True


async def add_text(ledger: Ledger, investigation: dict, title: str, text: str, *,
                   user: str) -> tuple[dict, bool]:
    if len(text) > MAX_TEXT_CHARS:
        raise MaterialError(413, f"notes are limited to {MAX_TEXT_CHARS:,} characters")
    data = text.encode("utf-8")
    sha = hashlib.sha256(data).hexdigest()
    if existing := _duplicate(ledger, investigation["id"], "sha256", sha):
        return existing, False
    return await _record(ledger, investigation, "text", title.strip()[:200], "text", len(data),
                         sha, text, user=user), True


async def add_link(ledger: Ledger, investigation: dict, url: str, *,
                   user: str) -> tuple[dict, bool]:
    """Read a page the analyst points at; the web_read finding is the material."""
    url = url.strip()
    for row in ledger.db.query("SELECT * FROM materials WHERE investigation_id = ? AND "
                               "kind = 'link'", (investigation["id"],)):
        if row["note"] == url or row["name"] == url:
            return row, False
    outcome = await read_page(ledger, investigation, url, user=user, run_by="analyst")
    if outcome.refused:
        raise MaterialError(403, outcome.error or "refused")
    if outcome.error:
        raise MaterialError(502, f"could not read {url}: {outcome.error}")
    raw = outcome.result.raw
    markdown = raw.get("markdown") or ""
    found = extract(markdown)
    found["emails"] = sorted(set(found["emails"]) | set(raw.get("emails") or []))[:50]
    found["company_identifiers"] = raw.get("company_identifiers") or found["company_identifiers"]
    material = {
        "id": new_id("m"), "investigation_id": investigation["id"], "kind": "link",
        "name": (raw.get("title") or url)[:200], "format": "web",
        "size_bytes": len(markdown.encode("utf-8")), "sha256": None, "chars": len(markdown),
        "file": None, "finding_id": outcome.finding_id, "note": url,
        "digest": {**digest(markdown, found), "url": raw.get("final_url") or url},
        "added_by": user, "created_at": now(),
    }
    return _store(ledger, investigation, material, user), True


async def _record(ledger: Ledger, investigation: dict, kind: str, name: str, fmt: str,
                  size: int, sha: str, markdown: str, *, user: str, file: str | None = None,
                  note: str | None = None) -> dict:
    material_id = new_id("m")
    found = extract(markdown)
    doc_key = sha
    entities = [EntityCandidate(EntityType.DOCUMENT, doc_key, {
        "kind": CONNECTOR, "name": name, "format": fmt, "material_id": material_id,
        "sha256": sha})]
    rels: list[RelationshipCandidate] = []
    for e in found["emails"]:
        entities.append(EntityCandidate(EntityType.EMAIL, e, match_quality=0.8))
        rels.append(RelationshipCandidate("mentions_email", EntityType.DOCUMENT, doc_key,
                                          EntityType.EMAIL, e))
    for ident in found["company_identifiers"]:
        entities.append(EntityCandidate(EntityType.ORGANIZATION, ident,
                                        {"identifier_seen_in": name}, match_quality=0.8))
        rels.append(RelationshipCandidate("names_company", EntityType.DOCUMENT, doc_key,
                                          EntityType.ORGANIZATION, ident))
    for link in found["urls"]:
        entities.append(EntityCandidate(EntityType.URL, link, match_quality=0.8))
        rels.append(RelationshipCandidate("links_to", EntityType.DOCUMENT, doc_key,
                                          EntityType.URL, link))
    kept = markdown[:MAX_MARKDOWN]
    result = ConnectorResult(
        source=CONNECTOR, passive=True,
        raw={"material_id": material_id, "kind": kind, "name": name, "format": fmt,
             "size_bytes": size, "sha256_original": sha,
             "converter": "markitdown" if fmt in ("pdf", "docx") else None,
             "markdown": kept, "truncated": len(markdown) > MAX_MARKDOWN, **found},
        entities=entities, relationships=rels, note=note)
    outcome = ledger.record(investigation, CONNECTOR, {"arg": name, "kind": kind,
                                                       "sha256": sha},
                            result, user=user, run_by="analyst")
    material = {
        "id": material_id, "investigation_id": investigation["id"], "kind": kind,
        "name": name, "format": fmt, "size_bytes": size, "sha256": sha, "chars": len(kept),
        "file": file, "finding_id": outcome.finding_id, "note": note,
        "digest": digest(kept, found), "added_by": user, "created_at": now(),
    }
    return _store(ledger, investigation, material, user)


def _store(ledger: Ledger, investigation: dict, material: dict, user: str) -> dict:
    ledger.db.insert("materials", material)
    ledger.db.audit(user=user, action="material.add", investigation_id=investigation["id"],
                    target=material["name"],
                    detail={"material_id": material["id"], "kind": material["kind"],
                            "format": material["format"], "sha256": material["sha256"],
                            "finding_id": material["finding_id"], "chars": material["chars"]})
    ledger.bus.publish(investigation["id"], "material.added", {"material": public(material)})
    return ledger.db.get("materials", material["id"])


# --- read / withdraw --------------------------------------------------------------------

def public(material: dict) -> dict:
    return {k: v for k, v in material.items() if k != "file"}


def text_of(ledger: Ledger, material: dict) -> str:
    """The material's markdown, from its finding's payload."""
    f = ledger.db.get("findings", material["finding_id"]) if material["finding_id"] else None
    if not f or not f["raw_path"]:
        return ""
    path = ledger.settings.data_dir / f["raw_path"]
    if not path.is_file():
        return ""
    raw = json.loads(path.read_text(encoding="utf-8")).get("raw") or {}
    return raw.get("markdown") or ""


def index(ledger: Ledger, investigation_id: str, only: list[str] | None = None) -> list[str]:
    """One line per material for an agent's brief: what it is, not what it says."""
    rows = ledger.db.query("SELECT * FROM materials WHERE investigation_id = ? "
                           "ORDER BY created_at", (investigation_id,))
    lines = []
    for m in rows:
        if only is not None and m["id"] not in only:
            continue
        d = m["digest"] or {}
        what = {"file": f"{m['format']} file", "text": "analyst note",
                "link": f"web page {d.get('url') or m['note']}"}[m["kind"]]
        line = f"- {m['id']} \"{m['name']}\" ({what}, {m['chars']:,} chars, finding {m['finding_id']})"
        if m["note"] and m["kind"] != "link":
            line += f" NOTE: {m['note']}."
        parts = []
        if d.get("outline"):
            parts.append("headings: " + "; ".join(d["outline"][:10]))
        for key, label in (("company_identifiers", "company ids"), ("emails", "emails"),
                           ("urls", "links")):
            values = d.get(key) or []
            if values:
                shown = ", ".join(values[:8]) + (f" (+{len(values) - 8} more)"
                                                 if len(values) > 8 else "")
                parts.append(f"{label}: {shown}")
        if d.get("preview"):
            parts.append(f"opens with: \"{d['preview'][:240]}\"")
        lines.append(line + ("\n  " + "\n  ".join(parts) if parts else ""))
    return lines


def withdraw(ledger: Ledger, material: dict, *, user: str) -> list[str]:
    """Remove a material: its files, its text and what only it put in the graph.

    The finding row stays (marked withdrawn) so the audit trail and the hash of
    what was supplied survive; the text does not. Refused while a report cites it.
    """
    inv_id = material["investigation_id"]
    fid = material["finding_id"]
    if fid and ledger.db.one("SELECT id FROM reports WHERE investigation_id = ? AND body LIKE ?",
                             (inv_id, f"%{fid}%")):
        raise MaterialError(409, "a report cites this material; remove the citation first")
    if material["file"]:
        (material_dir(ledger, inv_id) / material["file"]).unlink(missing_ok=True)
    removed: list[str] = []
    if fid:
        f = ledger.db.get("findings", fid)
        if f and f["raw_path"]:
            (ledger.settings.data_dir / f["raw_path"]).unlink(missing_ok=True)
        removed = ledger.resolver.drop_finding(fid)
        ledger.db.update("findings", fid, {"raw_path": None, "error": "withdrawn by analyst",
                                           "entity_count": 0})
    ledger.db.conn.execute("DELETE FROM materials WHERE id = ?", (material["id"],))
    ledger.db.conn.commit()
    ledger.db.audit(user=user, action="material.withdraw", investigation_id=inv_id,
                    target=material["name"],
                    detail={"material_id": material["id"], "finding_id": fid,
                            "entities_removed": len(removed)})
    ledger.bus.publish(inv_id, "material.removed", {"material_id": material["id"],
                                                     "entities_removed": removed})
    return removed
