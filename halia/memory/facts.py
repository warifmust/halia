"""User-controlled memory — facts halia recalls across runs.

Trust-first: the USER decides what halia remembers (`halia remember …`), it's
inspectable (`halia memory`) and editable (`halia forget …`), and it's injected
into halia's context each run. The agent does NOT write memory autonomously — an
agent that "learns" an unverified wrong fact is worse than one that doesn't.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from halia.store.database import DB_PATH, connect


@dataclass(frozen=True)
class Fact:
    """One remembered fact."""

    id: str
    created_at: str
    content: str


def remember(content: str, db_path: Path = DB_PATH) -> Fact:
    """Store a fact; returns it."""
    fact = Fact(uuid.uuid4().hex[:8], datetime.now(UTC).isoformat(), content.strip())
    conn = connect(db_path)
    try:
        conn.execute(
            "INSERT INTO memory (id, created_at, content) VALUES (?, ?, ?)",
            (fact.id, fact.created_at, fact.content),
        )
        conn.commit()
    finally:
        conn.close()
    return fact


def list_facts(db_path: Path = DB_PATH) -> list[Fact]:
    """All remembered facts, oldest first."""
    if not db_path.exists():
        return []
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, created_at, content FROM memory ORDER BY created_at"
        ).fetchall()
    finally:
        conn.close()
    return [Fact(row[0], row[1], row[2]) for row in rows]


def forget(fact_id: str, db_path: Path = DB_PATH) -> bool:
    """Remove a fact by id (or unique id prefix); True if anything was removed."""
    conn = connect(db_path)
    try:
        cursor = conn.execute(
            "DELETE FROM memory WHERE id = ? OR id LIKE ?", (fact_id, f"{fact_id}%")
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def memory_block(db_path: Path = DB_PATH) -> str:
    """Render remembered facts for injection into the system prompt (or '')."""
    facts = list_facts(db_path)
    if not facts:
        return ""
    lines = "\n".join(f"- {fact.content}" for fact in facts)
    return f"\n\nThings the user has asked you to remember:\n{lines}"
