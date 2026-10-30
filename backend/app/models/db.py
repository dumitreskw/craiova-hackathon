"""SQLite storage (docs/02-architecture.md#storage).

One file in WAL mode. All access happens on the event-loop thread (API
handlers and scans are ``async``), so a single connection is enough.

Raw connector payloads are written as JSON files under the evidence directory;
the ``findings`` row keeps the path and the payload's sha256, so the database
stays small and a purge can delete the files.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS investigations (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    purpose         TEXT NOT NULL,
    legal_basis     TEXT NOT NULL,
    mode            TEXT NOT NULL DEFAULT 'passive',      -- passive | active
    scope           TEXT NOT NULL DEFAULT '[]',           -- JSON list of hosts/domains/IPs
    authorization   TEXT,                                 -- who authorized active work, when, ref
    settings        TEXT NOT NULL DEFAULT '{}',
    retention_days  INTEGER NOT NULL,
    legal_hold      INTEGER NOT NULL DEFAULT 0,
    created_by      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    deleted_at      TEXT
);

CREATE TABLE IF NOT EXISTS targets (
    id               TEXT PRIMARY KEY,
    investigation_id TEXT NOT NULL REFERENCES investigations(id),
    value            TEXT NOT NULL,
    type             TEXT NOT NULL,
    entity_id        TEXT,
    added_by         TEXT NOT NULL,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS findings (
    id               TEXT PRIMARY KEY,
    investigation_id TEXT NOT NULL REFERENCES investigations(id),
    connector        TEXT NOT NULL,
    input            TEXT NOT NULL,                       -- JSON: the arguments it was called with
    entity_ref       TEXT,                                -- entity that triggered the call
    run_by           TEXT NOT NULL,                       -- quick_scan | pivot | agent:<run> | analyst
    passive          INTEGER NOT NULL,
    fetched_at       TEXT NOT NULL,
    duration_ms      INTEGER NOT NULL,
    sha256           TEXT,
    raw_path         TEXT,
    note             TEXT,
    error            TEXT,
    entity_count     INTEGER NOT NULL DEFAULT 0,
    egress           TEXT                                 -- where the request left from (OSINT_EGRESS_LABEL)
);

CREATE TABLE IF NOT EXISTS entities (
    id               TEXT PRIMARY KEY,
    investigation_id TEXT NOT NULL REFERENCES investigations(id),
    type             TEXT NOT NULL,
    key              TEXT NOT NULL,
    attributes       TEXT NOT NULL DEFAULT '{}',          -- {name: [{value, sources[]}]}
    confidence       REAL NOT NULL DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'active',      -- active | candidate | confirmed | rejected
    first_seen       TEXT NOT NULL,
    last_seen        TEXT NOT NULL,
    UNIQUE (investigation_id, type, key)
);

CREATE TABLE IF NOT EXISTS edges (
    id               TEXT PRIMARY KEY,
    investigation_id TEXT NOT NULL REFERENCES investigations(id),
    type             TEXT NOT NULL,
    from_id          TEXT NOT NULL REFERENCES entities(id),
    to_id            TEXT NOT NULL REFERENCES entities(id),
    attributes       TEXT NOT NULL DEFAULT '{}',
    confidence       REAL NOT NULL DEFAULT 0,
    first_seen       TEXT NOT NULL,
    last_seen        TEXT NOT NULL,
    UNIQUE (investigation_id, type, from_id, to_id)
);

-- Which findings support which entity / edge (provenance, confidence input).
CREATE TABLE IF NOT EXISTS entity_sources (
    entity_id     TEXT NOT NULL REFERENCES entities(id),
    finding_id    TEXT NOT NULL REFERENCES findings(id),
    reliability   REAL NOT NULL,
    match_quality REAL NOT NULL,
    PRIMARY KEY (entity_id, finding_id)
);

CREATE TABLE IF NOT EXISTS edge_sources (
    edge_id       TEXT NOT NULL REFERENCES edges(id),
    finding_id    TEXT NOT NULL REFERENCES findings(id),
    reliability   REAL NOT NULL,
    match_quality REAL NOT NULL,
    PRIMARY KEY (edge_id, finding_id)
);

CREATE TABLE IF NOT EXISTS notes (
    id         TEXT PRIMARY KEY,
    entity_id  TEXT NOT NULL REFERENCES entities(id),
    author     TEXT NOT NULL,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id               TEXT PRIMARY KEY,
    investigation_id TEXT NOT NULL REFERENCES investigations(id),
    kind             TEXT NOT NULL,                       -- quick_scan | collection | report
    goal             TEXT,
    status           TEXT NOT NULL,                       -- running | finished | failed | cancelled
    summary          TEXT,
    stats            TEXT NOT NULL DEFAULT '{}',
    transcript       TEXT NOT NULL DEFAULT '[]',          -- agents: text, tool calls, results
    started_at       TEXT NOT NULL,
    finished_at      TEXT
);

CREATE TABLE IF NOT EXISTS reports (
    id               TEXT PRIMARY KEY,
    investigation_id TEXT NOT NULL REFERENCES investigations(id),
    title            TEXT NOT NULL,
    body             TEXT NOT NULL,                       -- JSON, see app/report
    agent_run_id     TEXT,
    created_by       TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

-- Append-only: who queried what, when, why, and whether it was allowed.
CREATE TABLE IF NOT EXISTS audit (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ts               TEXT NOT NULL,
    user             TEXT NOT NULL,
    action           TEXT NOT NULL,
    investigation_id TEXT,
    connector        TEXT,
    target           TEXT,
    outcome          TEXT NOT NULL,                       -- ok | refused | error
    detail           TEXT NOT NULL DEFAULT '{}'
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
    BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
    BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;

CREATE INDEX IF NOT EXISTS ix_findings_inv ON findings(investigation_id);
CREATE INDEX IF NOT EXISTS ix_entities_inv ON entities(investigation_id);
CREATE INDEX IF NOT EXISTS ix_edges_inv ON edges(investigation_id);
CREATE INDEX IF NOT EXISTS ix_audit_inv ON audit(investigation_id, ts);
"""

JSON_COLUMNS = {"scope", "settings", "input", "attributes", "stats", "body", "detail",
                "transcript"}

# Columns added after the first release: (table, column, DDL). Applied on open,
# so an existing database file keeps working.
MIGRATIONS = [
    ("findings", "egress", "TEXT"),
    ("agent_runs", "transcript", "TEXT NOT NULL DEFAULT '[]'"),
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out = dict(row)
    for col in JSON_COLUMNS & out.keys():
        if isinstance(out[col], str):
            out[col] = json.loads(out[col])
    return out


class Database:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
        for table, column, ddl in MIGRATIONS:
            cols = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # --- generic helpers ----------------------------------------------------

    def insert(self, table: str, values: dict[str, Any]) -> None:
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        self.conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})",
                          [_encode(k, v) for k, v in values.items()])
        self.conn.commit()

    def update(self, table: str, row_id: str, values: dict[str, Any]) -> None:
        sets = ", ".join(f"{k} = ?" for k in values)
        self.conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?",
                          [*(_encode(k, v) for k, v in values.items()), row_id])
        self.conn.commit()

    def get(self, table: str, row_id: str) -> dict[str, Any] | None:
        return row_to_dict(
            self.conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone())

    def query(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        return [row_to_dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
        return row_to_dict(self.conn.execute(sql, params).fetchone())

    # --- audit --------------------------------------------------------------

    def audit(self, *, user: str, action: str, outcome: str = "ok",
              investigation_id: str | None = None, connector: str | None = None,
              target: str | None = None, detail: dict | None = None) -> None:
        self.insert("audit", {
            "ts": now(), "user": user, "action": action, "outcome": outcome,
            "investigation_id": investigation_id, "connector": connector,
            "target": target, "detail": detail or {},
        })


def _encode(column: str, value: Any) -> Any:
    if column in JSON_COLUMNS and not isinstance(value, str):
        return json.dumps(value, default=str)
    if isinstance(value, bool):
        return int(value)
    return value
