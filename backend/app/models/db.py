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
    mode            TEXT NOT NULL DEFAULT 'passive',      -- passive | active
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

-- Verdicts on whether an entity is really about the subject: by Claude's
-- verification agent or by an analyst (app/review.py). The latest one counts;
-- an analyst verdict is never overridden by the agent.
CREATE TABLE IF NOT EXISTS entity_reviews (
    id               TEXT PRIMARY KEY,
    investigation_id TEXT NOT NULL REFERENCES investigations(id),
    entity_id        TEXT NOT NULL REFERENCES entities(id),
    verdict          TEXT NOT NULL,                       -- confirmed | rejected | uncertain
    reason           TEXT NOT NULL,
    reviewer         TEXT NOT NULL,                       -- analyst name, or the agent run id
    by_agent         INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL
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

-- Material the analyst supplies (files, notes, links), converted to markdown.
-- The text lives in the finding's payload so claims can cite it like a lookup.
CREATE TABLE IF NOT EXISTS materials (
    id               TEXT PRIMARY KEY,
    investigation_id TEXT NOT NULL REFERENCES investigations(id),
    kind             TEXT NOT NULL,                       -- file | text | link
    name             TEXT NOT NULL,
    format           TEXT NOT NULL,                       -- pdf | docx | md | txt | text | web
    size_bytes       INTEGER NOT NULL DEFAULT 0,
    sha256           TEXT,                                -- of the original bytes
    chars            INTEGER NOT NULL DEFAULT 0,          -- length of the markdown
    file             TEXT,                                -- original upload, under evidence/
    finding_id       TEXT REFERENCES findings(id),
    note             TEXT,
    digest           TEXT NOT NULL DEFAULT '{}',          -- outline, identifiers, preview
    added_by         TEXT NOT NULL,
    created_at       TEXT NOT NULL
);

-- Research accounts ("personas") for social-media capture (app/social.py).
-- Only metadata: the logged-in browser profiles stay on the Kali VM.
CREATE TABLE IF NOT EXISTS personas (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    platform    TEXT NOT NULL,                            -- linkedin | facebook | instagram | x | ...
    region      TEXT NOT NULL,                            -- Mullvad location it always uses, e.g. de-fra
    status      TEXT NOT NULL DEFAULT 'needs_login',      -- ok | needs_login | disabled
    note        TEXT,
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    status_at   TEXT,
    last_used   TEXT
);

-- Work for the Kali worker, which polls for it (it can't be reached from here).
CREATE TABLE IF NOT EXISTS social_jobs (
    id               TEXT PRIMARY KEY,
    kind             TEXT NOT NULL,                       -- capture | login | check | search | interactions
    status           TEXT NOT NULL,                       -- queued | running | done | failed | expired
    persona          TEXT,
    mode             TEXT NOT NULL DEFAULT 'auto',        -- auto | persona | anonymous
    url              TEXT,
    query            TEXT,                                -- search text (kind = search)
    platform         TEXT,                                -- target platform (search / interactions)
    investigation_id TEXT REFERENCES investigations(id),
    entity_id        TEXT,
    watch_id         TEXT,
    requested_by     TEXT NOT NULL,
    run_by           TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    started_at       TEXT,
    finished_at      TEXT,
    error            TEXT,
    finding_id       TEXT,
    result           TEXT NOT NULL DEFAULT '{}'
);

-- Profiles re-captured on a schedule; each snapshot is diffed with the last.
CREATE TABLE IF NOT EXISTS social_watches (
    id               TEXT PRIMARY KEY,
    investigation_id TEXT NOT NULL REFERENCES investigations(id),
    entity_id        TEXT,
    url              TEXT NOT NULL,
    persona          TEXT,
    interval_hours   INTEGER NOT NULL,
    active           INTEGER NOT NULL DEFAULT 1,
    created_by       TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    last_run_at      TEXT,
    last_finding_id  TEXT,
    last_error       TEXT
);

-- Small key/value store for server-wide settings the analyst changes from the
-- UI (e.g. which model each agent task uses). Values are JSON.
CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL
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

-- --- Accounts and tenancy (app/auth) -------------------------------------------
-- Identities live in Keycloak; a user row mirrors one (``sub``) and holds what
-- the platform needs: workspace memberships and the platform-admin flag.
CREATE TABLE IF NOT EXISTS users (
    id               TEXT PRIMARY KEY,
    sub              TEXT NOT NULL UNIQUE,                -- Keycloak user id
    email            TEXT NOT NULL,
    name             TEXT NOT NULL DEFAULT '',
    platform_admin   INTEGER NOT NULL DEFAULT 0,          -- Keycloak realm role platform-admin
    default_org_id   TEXT,
    created_at       TEXT NOT NULL,
    last_login_at    TEXT
);

-- A workspace (tenant). Investigations, audit, members and API keys belong to one.
CREATE TABLE IF NOT EXISTS orgs (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    plan             TEXT NOT NULL DEFAULT 'free',        -- see app/auth/plans.py
    created_by       TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    disabled_at      TEXT
);

CREATE TABLE IF NOT EXISTS memberships (
    org_id           TEXT NOT NULL REFERENCES orgs(id),
    user_id          TEXT NOT NULL REFERENCES users(id),
    role             TEXT NOT NULL,                       -- owner | admin | analyst | viewer
    created_at       TEXT NOT NULL,
    PRIMARY KEY (org_id, user_id)
);

-- Single-use invitation links; only the token's sha256 is stored.
CREATE TABLE IF NOT EXISTS invitations (
    id               TEXT PRIMARY KEY,
    org_id           TEXT NOT NULL REFERENCES orgs(id),
    email            TEXT NOT NULL,
    role             TEXT NOT NULL,
    token_hash       TEXT NOT NULL UNIQUE,
    created_by       TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    expires_at       TEXT NOT NULL,
    accepted_by      TEXT,
    accepted_at      TEXT,
    revoked_at       TEXT
);

-- Browser sessions (HttpOnly cookie). ``id`` is the sha256 of the cookie value;
-- the Keycloak refresh token lets the app notice a disabled account or a
-- logout elsewhere within minutes.
CREATE TABLE IF NOT EXISTS sessions (
    id               TEXT PRIMARY KEY,
    user_id          TEXT NOT NULL REFERENCES users(id),
    org_id           TEXT,
    id_token         TEXT,
    refresh_token    TEXT,
    created_at       TEXT NOT NULL,
    last_seen_at     TEXT NOT NULL,
    checked_at       TEXT NOT NULL,
    expires_at       TEXT NOT NULL
);

-- Workspace API keys (REST API and the /mcp endpoint). Only the sha256 is stored.
CREATE TABLE IF NOT EXISTS api_keys (
    id               TEXT PRIMARY KEY,
    org_id           TEXT NOT NULL REFERENCES orgs(id),
    user_id          TEXT NOT NULL REFERENCES users(id),
    name             TEXT NOT NULL,
    prefix           TEXT NOT NULL,                       -- first characters, for display
    key_hash         TEXT NOT NULL UNIQUE,
    role             TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    last_used_at     TEXT,
    revoked_at       TEXT
);

-- In-flight sign-ins: OAuth state -> PKCE verifier, nonce, where to go after.
CREATE TABLE IF NOT EXISTS auth_states (
    id               TEXT PRIMARY KEY,                    -- the OAuth state value
    verifier         TEXT NOT NULL,
    nonce            TEXT NOT NULL,
    next             TEXT NOT NULL,
    created_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_memberships_user ON memberships(user_id);
CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions(user_id);

CREATE INDEX IF NOT EXISTS ix_findings_inv ON findings(investigation_id);
CREATE INDEX IF NOT EXISTS ix_entities_inv ON entities(investigation_id);
CREATE INDEX IF NOT EXISTS ix_edges_inv ON edges(investigation_id);
CREATE INDEX IF NOT EXISTS ix_audit_inv ON audit(investigation_id, ts);
CREATE INDEX IF NOT EXISTS ix_materials_inv ON materials(investigation_id);
CREATE INDEX IF NOT EXISTS ix_reviews_entity ON entity_reviews(entity_id, created_at);
CREATE INDEX IF NOT EXISTS ix_social_jobs_status ON social_jobs(status, created_at);
"""

JSON_COLUMNS = {"settings", "input", "attributes", "stats", "body", "detail",
                "transcript", "digest", "result"}

# Columns added after the first release: (table, column, DDL). Applied on open,
# so an existing database file keeps working.
MIGRATIONS = [
    ("findings", "egress", "TEXT"),
    ("social_jobs", "query", "TEXT"),
    ("social_jobs", "platform", "TEXT"),
    ("agent_runs", "transcript", "TEXT NOT NULL DEFAULT '[]'"),
    ("agent_runs", "model", "TEXT"),
    # Tenancy: the workspace an investigation / audit row belongs to. Rows from
    # before accounts existed have NULL and are claimed by the first platform
    # admin's workspace (app/auth/store.py).
    ("investigations", "org_id", "TEXT"),
    ("audit", "org_id", "TEXT"),
]

# Columns removed after a release: (table, column). Dropped on open, so an
# older database file doesn't reject inserts that no longer set them (a NOT
# NULL column the code stopped writing fails every INSERT).
DROPPED = [
    ("investigations", "legal_basis"),
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
        for table, column in DROPPED:
            cols = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if column in cols:
                self.conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        # Indexes on migrated columns: only once the column exists.
        self.conn.execute("CREATE INDEX IF NOT EXISTS ix_investigations_org "
                          "ON investigations(org_id, created_at)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS ix_audit_org ON audit(org_id, ts)")
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

    # --- settings (key/value) -----------------------------------------------

    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key: str, value: Any, user: str) -> None:
        self.conn.execute(
            "INSERT INTO app_settings (key, value, updated_by, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_by = excluded.updated_by, updated_at = excluded.updated_at",
            (key, json.dumps(value, default=str), user, now()))
        self.conn.commit()

    # --- audit --------------------------------------------------------------

    def audit(self, *, user: str, action: str, outcome: str = "ok",
              investigation_id: str | None = None, connector: str | None = None,
              target: str | None = None, detail: dict | None = None,
              org_id: str | None = None) -> None:
        if org_id is None and investigation_id:
            row = self.conn.execute("SELECT org_id FROM investigations WHERE id = ?",
                                    (investigation_id,)).fetchone()
            org_id = row[0] if row else None
        if org_id is None:
            # The workspace of whoever is acting (set per request, inherited by
            # the scans and agents a request starts).
            from ..auth.context import current_principal
            principal = current_principal()
            org_id = principal.org_id if principal else None
        self.insert("audit", {
            "ts": now(), "user": user, "action": action, "outcome": outcome,
            "investigation_id": investigation_id, "connector": connector,
            "target": target, "detail": detail or {}, "org_id": org_id,
        })


def _encode(column: str, value: Any) -> Any:
    if column in JSON_COLUMNS and not isinstance(value, str):
        return json.dumps(value, default=str)
    if isinstance(value, bool):
        return int(value)
    return value
