"""Entity verdicts: is this entity really about the subject?

Scans and sweeps produce plenty of noise: the namesake on a search page, the
username that exists on 60 unrelated sites, the third-party mail host. Claude's
verification agent (app/agent, kind ``verify``) reviews what matters for the
subject and records a verdict per entity, so the analyst only looks at what is
genuinely uncertain and the report writer only sees what survived.

Rules:

* ``confirmed`` / ``rejected`` set the entity's status (the same switch as the
  analyst's confirm / reject); ``uncertain`` leaves the status alone and keeps
  the reason for the analyst.
* An analyst's verdict is final: the agent never overrides it.
* The agent may reject a sanctions / wanted-list name match, never confirm one;
  confirming a legal hit stays a human decision with a named disambiguator.
* Targets are never rejected (the analyst chose them).
"""

from __future__ import annotations

from typing import Any

from .events import EventBus
from .models import Database, new_id, now
from .resolver import Resolver, best_attributes, entity_view

VERDICTS = ("confirmed", "rejected", "uncertain")

# What the subject kind makes worth reviewing. Infrastructure that DNS and
# certificate logs return is deterministic (no namesakes), so for a domain
# target it is not reviewed; for a person or company it is mostly irrelevant
# and only the domains are reviewed (is this the subject's website or not).
PERSONAL_TYPES = ["Person", "Organization", "SocialProfile", "Username", "Email", "Phone",
                  "Location", "Breach", "Repository", "URL", "Domain"]
DOMAIN_TYPES = ["Person", "Organization", "SocialProfile", "Username", "Email", "Phone",
                "Repository", "URL"]


def subject_kind(db: Database, investigation_id: str) -> str:
    """``person``, ``organization`` or ``domain``: what the report is about."""
    types = {r["type"] for r in db.query(
        "SELECT type FROM targets WHERE investigation_id = ?", (investigation_id,))}
    if "Person" in types or "Email" in types or "Username" in types or "Phone" in types:
        return "person"
    if "Organization" in types:
        return "organization"
    return "domain"


def review_types(kind: str) -> list[str]:
    return DOMAIN_TYPES if kind == "domain" else PERSONAL_TYPES


def latest(db: Database, entity_id: str) -> dict | None:
    return db.one("SELECT * FROM entity_reviews WHERE entity_id = ? ORDER BY rowid DESC LIMIT 1",
                  (entity_id,))


def target_entity_ids(db: Database, investigation_id: str) -> set[str]:
    return {r["entity_id"] for r in db.query(
        "SELECT entity_id FROM targets WHERE investigation_id = ? AND entity_id IS NOT NULL",
        (investigation_id,))}


class ReviewError(ValueError):
    pass


def record(db: Database, resolver: Resolver, bus: EventBus, entity_id: str, verdict: str,
           reason: str, *, reviewer: str, user: str, by_agent: bool) -> dict:
    """Store a verdict and apply it to the entity. Returns the entity view."""
    if verdict not in VERDICTS:
        raise ReviewError(f"verdict must be one of {', '.join(VERDICTS)}")
    row = db.get("entities", entity_id)
    if row is None:
        raise ReviewError(f"no entity {entity_id}")
    inv_id = row["investigation_id"]
    if by_agent:
        prev = latest(db, entity_id)
        if prev and not prev["by_agent"]:
            raise ReviewError(f"{entity_id} was reviewed by an analyst ({prev['verdict']}); "
                              "that verdict stands")
        if verdict == "confirmed" and row["status"] == "candidate":
            raise ReviewError(f"{entity_id} is a sanctions/wanted-list name match: only an "
                              "analyst can confirm it; reject it or mark it uncertain")
        if verdict == "rejected" and entity_id in target_entity_ids(db, inv_id):
            raise ReviewError(f"{entity_id} is a target; it can't be rejected")
    reason = (reason or "").strip()[:600] or "(no reason given)"
    db.insert("entity_reviews", {"id": new_id("rv"), "investigation_id": inv_id,
                                 "entity_id": entity_id, "verdict": verdict, "reason": reason,
                                 "reviewer": reviewer, "by_agent": by_agent,
                                 "created_at": now()})
    if verdict != "uncertain" and row["status"] != verdict:
        resolver.set_status(entity_id, verdict)
    db.audit(user=user, action=f"entity.{verdict}", investigation_id=inv_id, target=row["key"],
             detail={"entity_id": entity_id, "reason": reason, "previous_status": row["status"],
                     "by": reviewer if by_agent else "analyst"})
    view = entity_view(db.get("entities", entity_id))
    bus.publish(inv_id, "entity.updated", {"entity": view})
    return view


def queue(db: Database, investigation_id: str, *, offset: int = 0, limit: int = 40,
          include_reviewed: bool = False) -> dict[str, Any]:
    """Entities worth a verdict, with enough context to judge each one."""
    kind = subject_kind(db, investigation_id)
    types = review_types(kind)
    marks = ",".join("?" * len(types))
    targets = target_entity_ids(db, investigation_id)
    rows = db.query(
        f"SELECT * FROM entities WHERE investigation_id = ? AND type IN ({marks}) "
        "AND status IN ('active', 'candidate', 'confirmed') "
        "ORDER BY CASE status WHEN 'candidate' THEN 0 ELSE 1 END, type, confidence DESC, key",
        (investigation_id, *types))
    pending = []
    for r in rows:
        if r["id"] in targets:
            continue
        review = latest(db, r["id"])
        if review and not include_reviewed:
            continue
        pending.append(r)
    page = pending[offset:offset + limit]
    items = []
    for r in page:
        attrs = {k: (str(v)[:200] if not isinstance(v, (int, float, bool)) else v)
                 for k, v in best_attributes(r["attributes"]).items()
                 if k not in ("sha256", "file", "raw_path")}
        sources = db.query(
            "SELECT f.id, f.connector, f.input FROM entity_sources es JOIN findings f "
            "ON f.id = es.finding_id WHERE es.entity_id = ? ORDER BY f.fetched_at LIMIT 5",
            (r["id"],))
        links = db.query(
            "SELECT e.type AS rel, o.id, o.type, o.key FROM edges e JOIN entities o "
            "ON o.id = CASE WHEN e.from_id = ? THEN e.to_id ELSE e.from_id END "
            "WHERE e.from_id = ? OR e.to_id = ? LIMIT 6", (r["id"], r["id"], r["id"]))
        items.append({
            "id": r["id"], "type": r["type"], "key": r["key"], "status": r["status"],
            "confidence": round(r["confidence"], 2), "attributes": attrs,
            "sources": [{"finding": s["id"], "connector": s["connector"],
                         "looked_up": (s["input"] or {}).get("arg")} for s in sources],
            "linked_to": [{"rel": x["rel"], "id": x["id"], "type": x["type"], "key": x["key"]}
                          for x in links],
        })
    return {"subject_kind": kind, "total_pending": len(pending), "offset": offset,
            "next_offset": offset + limit if offset + limit < len(pending) else None,
            "items": items}


def summary(db: Database, investigation_id: str) -> dict[str, Any]:
    """Counts of the latest verdict per entity, and how many still await one."""
    rows = db.query(
        "SELECT r.verdict FROM entity_reviews r WHERE r.investigation_id = ? AND r.rowid = "
        "(SELECT max(rowid) FROM entity_reviews x WHERE x.entity_id = r.entity_id)",
        (investigation_id,))
    out: dict[str, Any] = {v: 0 for v in VERDICTS}
    for r in rows:
        out[r["verdict"]] += 1
    out["pending"] = queue(db, investigation_id, limit=0)["total_pending"]
    return out
