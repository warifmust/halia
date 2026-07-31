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
    id         TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    provider   TEXT NOT NULL,
    model      TEXT NOT NULL,
    prompt     TEXT NOT NULL,
    answer     TEXT NOT NULL,
    steps_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_started_at ON runs (started_at DESC);
"""


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Open the database (creating the dir + schema on first use)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    return conn
