"""Retention and purge (docs/06-legal-ethics.md, principle 4).

An investigation's evidence expires ``retention_days`` after creation unless it
is on legal hold. Purging deletes the graph, findings, reports and evidence
files. The audit log is kept: it records that the data existed and was
deleted, which is what accountability needs.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone

from .config import Settings
from .models import Database, now

# Children before parents (foreign keys).
_PURGE_SQL = [
    "DELETE FROM notes WHERE entity_id IN (SELECT id FROM entities WHERE investigation_id = ?)",
    ("DELETE FROM entity_sources WHERE entity_id IN "
     "(SELECT id FROM entities WHERE investigation_id = ?)"),
    "DELETE FROM edge_sources WHERE edge_id IN (SELECT id FROM edges WHERE investigation_id = ?)",
    "DELETE FROM edges WHERE investigation_id = ?",
    "DELETE FROM entities WHERE investigation_id = ?",
    "DELETE FROM materials WHERE investigation_id = ?",
    "DELETE FROM findings WHERE investigation_id = ?",
    "DELETE FROM reports WHERE investigation_id = ?",
    "DELETE FROM agent_runs WHERE investigation_id = ?",
    "DELETE FROM targets WHERE investigation_id = ?",
]


def purge_investigation(db: Database, settings: Settings, investigation_id: str,
                        user: str, reason: str) -> None:
    for sql in _PURGE_SQL:
        db.conn.execute(sql, (investigation_id,))
    db.conn.execute("UPDATE investigations SET deleted_at = ?, scope = '[]', "
                    "authorization = NULL WHERE id = ?", (now(), investigation_id))
    db.conn.commit()
    shutil.rmtree(settings.evidence_dir / investigation_id, ignore_errors=True)
    db.audit(user=user, action="investigation.purge", investigation_id=investigation_id,
             detail={"reason": reason})


def purge_expired(db: Database, settings: Settings, user: str = "system") -> list[str]:
    purged = []
    current = datetime.now(timezone.utc)
    for inv in db.query("SELECT id, created_at, retention_days FROM investigations "
                        "WHERE deleted_at IS NULL AND legal_hold = 0"):
        expires = datetime.fromisoformat(inv["created_at"]) + timedelta(days=inv["retention_days"])
        if expires <= current:
            purge_investigation(db, settings, inv["id"], user, "retention expired")
            purged.append(inv["id"])
    return purged
