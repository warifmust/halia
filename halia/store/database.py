"""The single local SQLite database for all of halia's data.

One self-hosted file at `~/.halia/halia.db` — audit runs now, memory later.
Chosen over scattered JSON files: queryable history, one place, and the
foundation the memory layer builds on. `~/.halia` is inside the permission
floor, so the agent can't read its own database.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path.home() / ".halia" / "halia.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id              TEXT PRIMARY KEY,
    started_at      TEXT NOT NULL,
    provider        TEXT NOT NULL,
    model           TEXT NOT NULL,
    prompt          TEXT NOT NULL,
    answer          TEXT NOT NULL,
    steps_json      TEXT NOT NULL,
    plan            TEXT NOT NULL DEFAULT '',
    unverified_json TEXT NOT NULL DEFAULT '[]',
    corrections     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_runs_started_at ON runs (started_at DESC);

CREATE TABLE IF NOT EXISTS memory (
    id         TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    content    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profiles (
    name         TEXT PRIMARY KEY,
    skills_json  TEXT NOT NULL,
    model        TEXT,
    extra_prompt TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS checkpoints (
    id            TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    prompt        TEXT NOT NULL,
    provider      TEXT NOT NULL,
    model         TEXT NOT NULL,
    skills_json   TEXT NOT NULL,
    extra_system  TEXT NOT NULL DEFAULT '',
    plan          TEXT NOT NULL DEFAULT '',
    messages_json TEXT NOT NULL,
    steps_json    TEXT NOT NULL,
    pending_json  TEXT NOT NULL,
    iters_used    INTEGER NOT NULL DEFAULT 0,
    corrections   INTEGER NOT NULL DEFAULT 0,
    reason        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_checkpoints_created_at ON checkpoints (created_at DESC);

CREATE TABLE IF NOT EXISTS procedures (
    name                TEXT PRIMARY KEY,
    description         TEXT NOT NULL DEFAULT '',
    target             TEXT NOT NULL DEFAULT '',
    data_spec          TEXT NOT NULL DEFAULT '',
    data_source        TEXT NOT NULL DEFAULT 'synthesize',
    steps_json         TEXT NOT NULL DEFAULT '[]',
    method             TEXT NOT NULL DEFAULT 'GET',
    url                TEXT NOT NULL DEFAULT '',
    headers_json       TEXT NOT NULL DEFAULT '{}',
    result_columns_json TEXT NOT NULL DEFAULT '[]',
    pass_rule          TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id             TEXT PRIMARY KEY,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    title          TEXT NOT NULL DEFAULT '',
    provider       TEXT NOT NULL,
    model          TEXT NOT NULL,
    profile        TEXT,
    allow_commands INTEGER NOT NULL DEFAULT 0,
    messages_json  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_updated_at ON sessions (updated_at DESC);

CREATE TABLE IF NOT EXISTS images (
    id              TEXT PRIMARY KEY,
    stored_at       TEXT NOT NULL,
    original_path   TEXT NOT NULL,
    filename        TEXT NOT NULL,
    mime_type       TEXT NOT NULL DEFAULT 'image/png',
    width           INTEGER NOT NULL DEFAULT 0,
    height          INTEGER NOT NULL DEFAULT 0,
    size_bytes      INTEGER NOT NULL DEFAULT 0,
    content_hash    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_images_content_hash ON images (content_hash);

CREATE TABLE IF NOT EXISTS ref_files (
    id              TEXT PRIMARY KEY,
    stored_at       TEXT NOT NULL,
    original_path   TEXT NOT NULL,
    filename        TEXT NOT NULL,
    stored_filename TEXT NOT NULL DEFAULT '',
    file_type       TEXT NOT NULL DEFAULT '',
    profile         TEXT NOT NULL DEFAULT '',
    size_bytes      INTEGER NOT NULL DEFAULT 0,
    description     TEXT NOT NULL DEFAULT '',
    url             TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_ref_files_profile ON ref_files (profile);
"""


# Columns added to `runs` after its first release — applied to pre-existing DBs on
# open (CREATE TABLE IF NOT EXISTS won't add columns to a table that already exists).
_RUNS_MIGRATIONS = {
    "plan": "TEXT NOT NULL DEFAULT ''",
    "unverified_json": "TEXT NOT NULL DEFAULT '[]'",
    "corrections": "INTEGER NOT NULL DEFAULT 0",
}

# Columns added to `procedures` after its first release.
_PROCEDURES_MIGRATIONS = {
    "data_source": "TEXT NOT NULL DEFAULT 'synthesize'",
    "steps_json": "TEXT NOT NULL DEFAULT '[]'",
}

# Columns added to `sessions` after its first release. `archived_messages_json` keeps the
# full transcript that compaction summarised out of the working window.
_SESSIONS_MIGRATIONS = {
    "archived_messages_json": "TEXT NOT NULL DEFAULT '[]'",
}

# Columns added to `ref_files` after its first release (id/stored_at/original_path/filename
# were the original set). Older DBs may lack any of these — _ensure_columns adds the missing
# ones. (stored_filename was previously added to the schema without a migration, which broke
# learn_from_reference on pre-existing DBs; hence the full list here.)
_REF_FILES_MIGRATIONS = {
    "stored_filename": "TEXT NOT NULL DEFAULT ''",
    "file_type": "TEXT NOT NULL DEFAULT ''",
    "profile": "TEXT NOT NULL DEFAULT ''",
    "size_bytes": "INTEGER NOT NULL DEFAULT 0",
    "description": "TEXT NOT NULL DEFAULT ''",
    "url": "TEXT NOT NULL DEFAULT ''",
}


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    """Add any missing columns to `table` (idempotent, additive-only migration)."""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, ddl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Open the database (creating the dir + schema, migrating older DBs, on first use)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    _ensure_columns(conn, "runs", _RUNS_MIGRATIONS)
    _ensure_columns(conn, "procedures", _PROCEDURES_MIGRATIONS)
    _ensure_columns(conn, "sessions", _SESSIONS_MIGRATIONS)
    _ensure_columns(conn, "ref_files", _REF_FILES_MIGRATIONS)
    conn.commit()
    return conn
