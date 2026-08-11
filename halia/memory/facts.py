"""User-controlled memory — facts halia recalls across runs.

Trust-first: the USER decides what halia remembers (`halia remember …`), it's
inspectable (`halia memory`) and editable (`halia forget …`), and it's injected
into halia's context each run. The agent does NOT write memory autonomously — an
agent that "learns" an unverified wrong fact is worse than one that doesn't.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from halia.store.database import DB_PATH, connect

# Above this many facts, inject only the top-k *relevant* to the current prompt (via FTS5)
# instead of dumping all. At or below it, behave exactly as before (dump all).
_RECALL_THRESHOLD = 20
_RECALL_K = 8
# Common words that add noise to a keyword recall query.
_STOP = frozenset(
    {"the", "a", "an", "and", "or", "of", "to", "in", "is", "it", "for", "on", "with",
     "this", "that", "you", "your", "are", "was", "can", "do", "does", "how", "what", "please"}
)


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


def _fts_query(text: str) -> str | None:
    """Turn free text into a safe FTS5 query: quoted keyword terms OR'd together.

    Quoting each term makes it a literal string, so a raw prompt's punctuation (`*`, `:`,
    `-`, quotes) can't break MATCH or change its meaning. Returns None if nothing useful.
    """
    tokens = re.findall(r"[A-Za-z0-9]+", text.lower())
    terms = [t for t in dict.fromkeys(tokens) if len(t) >= 3 and t not in _STOP]
    if not terms:
        return None
    return " OR ".join(f'"{t}"' for t in terms[:20])  # cap terms to bound the query


def recall(query: str, k: int = _RECALL_K, db_path: Path = DB_PATH) -> list[Fact]:
    """Facts most relevant to `query` by BM25 ranking, best first.

    Returns [] if FTS5 is unavailable, the DB doesn't exist, or nothing matches — callers
    then fall back to another selection.
    """
    fts_q = _fts_query(query)
    if not fts_q or not db_path.exists():
        return []
    conn = connect(db_path)
    try:
        ranked = conn.execute(
            "SELECT fact_id FROM memory_fts WHERE memory_fts MATCH ? ORDER BY rank LIMIT ?",
            (fts_q, k),
        ).fetchall()
        ids = [r[0] for r in ranked]
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT id, created_at, content FROM memory WHERE id IN ({placeholders})", ids
        ).fetchall()
    except sqlite3.OperationalError:
        return []  # no FTS table on this build — degrade gracefully
    finally:
        conn.close()
    by_id = {r[0]: Fact(r[0], r[1], r[2]) for r in rows}
    return [by_id[i] for i in ids if i in by_id]  # preserve BM25 order


def memory_block(query: str | None = None, db_path: Path = DB_PATH) -> str:
    """Render remembered facts for injection into the system prompt (or '').

    With few facts, all are injected (unchanged). Above `_RECALL_THRESHOLD`, only the top-k
    facts *relevant to `query`* are injected (FTS5 BM25) — or the most-recent k if there's no
    query or no match — so a large memory doesn't flood the context.
    """
    facts = list_facts(db_path)
    if not facts:
        return ""
    if len(facts) > _RECALL_THRESHOLD:
        selected = recall(query, k=_RECALL_K, db_path=db_path) if query else []
        facts = selected or facts[-_RECALL_K:]  # relevant, else most-recent (bounded)
    lines = "\n".join(f"- {fact.content}" for fact in facts)
    return f"\n\nThings the user has asked you to remember:\n{lines}"
