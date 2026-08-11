"""Failure memory (Tier 2) — objective run failures halia recalls to avoid repeating them.

When a run ends in a HARD, objective failure (iteration cap, provider/tool error) it is recorded
here with its cause. On a later, similar task the FTS5 index surfaces it as an ADVISORY — never
as a learned fact. Objective events only (no self-assessment: a conscience correction or a
flagged figure is the trust floor WORKING, not a failure). Inspectable and forgettable, so a
stale lesson can be pruned; bounded in size.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from halia.memory.facts import _fts_query
from halia.store.database import DB_PATH, connect

_MAX_FAILURES = 200  # keep the store bounded — oldest beyond this are dropped
_RECALL_K = 3
_CAUSE_MAX = 200  # trim long error strings


@dataclass(frozen=True)
class Failure:
    id: str
    created_at: str
    prompt: str
    cause: str
    profile: str


def record_failure(
    prompt: str, cause: str, profile: str = "", db_path: Path = DB_PATH
) -> Failure | None:
    """Record an objective run failure. Best-effort — never raises into the caller."""
    prompt = (prompt or "").strip()
    cause = (cause or "").strip()[:_CAUSE_MAX]
    if not prompt or not cause:
        return None
    fail = Failure(
        uuid.uuid4().hex[:8], datetime.now(UTC).isoformat(), prompt, cause, profile or ""
    )
    try:
        conn = connect(db_path)
        try:
            conn.execute(
                "INSERT INTO failures (id, created_at, prompt, cause, profile) VALUES (?,?,?,?,?)",
                (fail.id, fail.created_at, fail.prompt, fail.cause, fail.profile),
            )
            conn.execute(
                "DELETE FROM failures WHERE id IN ("
                "SELECT id FROM failures ORDER BY created_at DESC LIMIT -1 OFFSET ?)",
                (_MAX_FAILURES,),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    return fail


def list_failures(db_path: Path = DB_PATH) -> list[Failure]:
    """All recorded failures, newest first."""
    if not db_path.exists():
        return []
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, created_at, prompt, cause, profile FROM failures ORDER BY created_at DESC"
        ).fetchall()
    finally:
        conn.close()
    return [Failure(*r) for r in rows]


def forget_failure(fail_id: str, db_path: Path = DB_PATH) -> bool:
    """Remove a failure by id (or unique id prefix); True if anything was removed."""
    conn = connect(db_path)
    try:
        cur = conn.execute(
            "DELETE FROM failures WHERE id = ? OR id LIKE ?", (fail_id, f"{fail_id}%")
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def recall_failures(query: str, k: int = _RECALL_K, db_path: Path = DB_PATH) -> list[Failure]:
    """Past failures whose task (prompt) is most similar to `query` by BM25, best first.

    Returns [] if FTS5 is unavailable, the DB doesn't exist, or nothing matches.
    """
    fts_q = _fts_query(query) if query else None
    if not fts_q or not db_path.exists():
        return []
    conn = connect(db_path)
    try:
        ranked = conn.execute(
            "SELECT fail_id FROM failures_fts WHERE failures_fts MATCH ? ORDER BY rank LIMIT ?",
            (fts_q, k),
        ).fetchall()
        ids = [r[0] for r in ranked]
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT id, created_at, prompt, cause, profile FROM failures WHERE id IN "
            f"({placeholders})",
            ids,
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
    by_id = {r[0]: Failure(*r) for r in rows}
    return [by_id[i] for i in ids if i in by_id]  # preserve BM25 order


def failures_advisory(query: str, db_path: Path = DB_PATH) -> str:
    """A short, LABELLED advisory about similar past failures for system-prompt injection (or '').

    Framed as an advisory grounded in past objective events — explicitly not an established fact —
    so the model treats it as a hint to verify for the current task, not a conclusion.
    """
    hits = recall_failures(query, db_path=db_path)
    if not hits:
        return ""
    lines = "\n".join(f"- a similar past task failed with: {h.cause}" for h in hits)
    return (
        "\n\nADVISORY — objective failures on similar past runs (past events, NOT established "
        "facts; use as a hint and verify for THIS task):\n" + lines
    )
