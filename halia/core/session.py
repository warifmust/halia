"""Sessions — a conversation that survives a restart (chat Tier 2).

A `halia chat` conversation lives in memory (Tier 1). A session persists that
transcript to the store so you can close chat, come back later, and `halia chat
--resume <id>` picks up where you left off. The whole `messages` list (system +
every turn, including tool exchanges) is saved after each turn; the profile and
allow_commands are stored too, so the resumed session rebuilds the same tools.

Old context can drift — a resumed session shows how long it has been idle so you
know whether the thread is still fresh. Secrets are never stored here.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from halia.providers.base import Message
from halia.store.database import DB_PATH, connect

_TITLE_MAX = 60


@dataclass(frozen=True)
class Session:
    """A persisted chat conversation."""

    id: str
    created_at: str  # ISO 8601, UTC
    updated_at: str
    title: str
    provider: str
    model: str
    profile: str | None
    allow_commands: bool
    messages: list[Message]

    def turn_count(self) -> int:
        """Number of user turns in the conversation."""
        return sum(1 for m in self.messages if m.get("role") == "user")


def new_session(
    provider: str,
    model: str,
    profile: str | None,
    allow_commands: bool,
    messages: list[Message],
) -> Session:
    """Build a fresh Session with a new id and timestamps."""
    now = datetime.now(UTC).isoformat()
    return Session(
        id=uuid.uuid4().hex[:12],
        created_at=now,
        updated_at=now,
        title="",
        provider=provider,
        model=model,
        profile=profile,
        allow_commands=allow_commands,
        messages=list(messages),
    )


def _derive_title(messages: list[Message]) -> str:
    """A short title from the first user message."""
    for m in messages:
        if m.get("role") == "user":
            text = str(m.get("content", "")).strip().replace("\n", " ")
            return text[:_TITLE_MAX]
    return "(empty)"


_COLUMNS = (
    "id, created_at, updated_at, title, provider, model, profile, "
    "allow_commands, messages_json"
)


def save_session(session: Session, db_path: Path = DB_PATH) -> None:
    """Persist a session, stamping `updated_at` and deriving a title if it has none."""
    title = session.title or _derive_title(session.messages)
    conn = connect(db_path)
    try:
        conn.execute(
            f"INSERT OR REPLACE INTO sessions ({_COLUMNS}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session.id,
                session.created_at,
                datetime.now(UTC).isoformat(),  # updated_at = now
                title,
                session.provider,
                session.model,
                session.profile,
                int(session.allow_commands),
                json.dumps(session.messages),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _row_to_session(row: Any) -> Session:
    return Session(
        id=row[0],
        created_at=row[1],
        updated_at=row[2],
        title=row[3],
        provider=row[4],
        model=row[5],
        profile=row[6],
        allow_commands=bool(row[7]),
        messages=list(json.loads(row[8])),
    )


def get_session(session_id: str, db_path: Path = DB_PATH) -> Session | None:
    """Load one session by id or unique prefix; None if missing or ambiguous."""
    if not db_path.exists():
        return None
    conn = connect(db_path)
    try:
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM sessions WHERE id LIKE ? ORDER BY updated_at DESC LIMIT 2",
            (session_id + "%",),
        ).fetchall()
    finally:
        conn.close()
    if len(rows) != 1:
        return None
    return _row_to_session(rows[0])


def list_sessions(db_path: Path = DB_PATH, limit: int = 20) -> list[Session]:
    """The most recently active sessions (newest first)."""
    if not db_path.exists():
        return []
    conn = connect(db_path)
    try:
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM sessions ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_session(row) for row in rows]


def delete_session(session_id: str, db_path: Path = DB_PATH) -> bool:
    """Delete a session by exact id. True if it existed."""
    conn = connect(db_path)
    try:
        cursor = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
