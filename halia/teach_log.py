"""Teach log — track format teaching events for learning analytics.

Records each time a user teaches a format (via /teach, /teach --paste, or
URL fetch). The log is used to detect patterns: if the user teaches similar
formats multiple times, halia can suggest consolidating them.

This is purely observational — halia does NOT write to the log autonomously.
The CLI handlers call record_teach() after a successful store_reference().
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from halia.store.database import DB_PATH, connect


@dataclass(frozen=True)
class TeachEvent:
    """One recorded teach event."""

    id: str
    created_at: str
    source: str  # file path, URL, or "paste"
    columns: str  # comma-separated column names (extracted from description)
    profile: str
    ref_id: str  # the ref_files.id this event created


def record_teach(
    source: str,
    columns: str = "",
    profile: str = "",
    ref_id: str = "",
    db_path: Path = DB_PATH,
) -> TeachEvent:
    """Record a teach event. Called after a successful store_reference()."""
    event = TeachEvent(
        id=uuid.uuid4().hex[:8],
        created_at=datetime.now(UTC).isoformat(),
        source=source,
        columns=columns,
        profile=profile,
        ref_id=ref_id,
    )
    conn = connect(db_path)
    try:
        conn.execute(
            "INSERT INTO teach_log (id, created_at, source, columns, profile, ref_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (event.id, event.created_at, event.source,
             event.columns, event.profile, event.ref_id),
        )
        conn.commit()
    finally:
        conn.close()
    return event


def extract_columns_from_description(description: str) -> str:
    """Extract column names from a format spec description or raw content.

    Parses patterns like:
    - "Headers: A, B, C"
    - "Columns: A | B | C"
    - "  - Test ID (string)"
    - "Test ID | Test Objective | Steps" (raw pipe-separated)
    - "Test ID, Test Objective, Steps" (raw comma-separated)

    Returns comma-separated column names, or empty string if none found.
    """
    import re

    if not description:
        return ""

    desc = description.lower()

    # Try explicit prefix patterns
    for prefix in ("headers:", "columns:", "format:", "fields:"):
        if prefix in desc:
            idx = desc.index(prefix) + len(prefix)
            chunk = desc[idx:idx + 500].split("\n")[0].strip()
            # Strip parenthesized type info
            chunk = re.sub(r"\([^)]*\)", "", chunk)
            if "," in chunk:
                cols = [c.strip() for c in chunk.split(",")]
            elif "|" in chunk:
                cols = [c.strip() for c in chunk.split("|")]
            else:
                continue
            cols = [c for c in cols if c and len(c) > 1]
            if cols:
                return ", ".join(cols)

    # Try pipe-separated headers (e.g., from paste: "Test ID | Objective | Steps")
    lines = description.strip().split("\n")
    for line in lines:
        line = line.strip()
        if "|" in line and not line.startswith("|"):
            parts = [p.strip() for p in line.split("|")]
            parts = [p for p in parts if p and len(p) > 1]
            if len(parts) >= 2:  # at least 2 columns to be a header row
                return ", ".join(parts)

    # Try comma-separated (e.g., "Test ID, Test Objective, Steps")
    for line in lines:
        line = line.strip()
        if "," in line and not line.startswith(","):
            parts = [p.strip() for p in line.split(",")]
            parts = [p for p in parts if p and len(p) > 1]
            if len(parts) >= 2:
                return ", ".join(parts)

    return ""


def find_similar_teaches(
    columns: str,
    profile: str = "",
    limit: int = 5,
    db_path: Path = DB_PATH,
) -> list[TeachEvent]:
    """Find teach events with similar column sets.

    Returns events that share at least 50% of columns with the given set.
    Useful for detecting when the user keeps teaching similar formats.
    """
    if not columns:
        return []

    target_cols = set(c.strip().lower() for c in columns.split(",") if c.strip())
    if not target_cols:
        return []

    conn = connect(db_path)
    try:
        if profile:
            rows = conn.execute(
                "SELECT id, created_at, source, columns, profile, ref_id "
                "FROM teach_log WHERE profile = ? ORDER BY created_at DESC",
                (profile,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, created_at, source, columns, profile, ref_id "
                "FROM teach_log ORDER BY created_at DESC"
            ).fetchall()
    finally:
        conn.close()

    matches: list[TeachEvent] = []
    for row in rows:
        event_cols = set(
            c.strip().lower() for c in row[3].split(",") if c.strip()
        )
        if not event_cols:
            continue
        # Check overlap: at least 50% of either set matches
        overlap = target_cols & event_cols
        if len(overlap) >= max(len(target_cols), len(event_cols)) * 0.5:
            matches.append(TeachEvent(*row))
        if len(matches) >= limit:
            break

    return matches


def list_teach_history(
    profile: str | None = None,
    limit: int = 20,
    db_path: Path = DB_PATH,
) -> list[TeachEvent]:
    """List recent teach events, optionally filtered by profile."""
    conn = connect(db_path)
    try:
        if profile:
            rows = conn.execute(
                "SELECT id, created_at, source, columns, profile, ref_id "
                "FROM teach_log WHERE profile = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (profile, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, created_at, source, columns, profile, ref_id "
                "FROM teach_log ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    finally:
        conn.close()
    return [TeachEvent(*row) for row in rows]


# Need Path for type hint
from pathlib import Path  # noqa: E402
