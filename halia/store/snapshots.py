"""File-write snapshots — a recovery path for `write_file` overwrites.

Before `write_file` overwrites an existing file, the original bytes are copied to
`~/.halia/snapshots/` and indexed in the DB. `halia undo` restores the most recent
snapshot for a path, with *pop* semantics: a successful restore removes that
snapshot, so a repeated undo peels back to the version before it.

`~/.halia` is inside the permission floor, so the agent can't read or tamper with
its own backups. Snapshotting is best-effort — it never raises into the caller's
write path (a failed backup must not block the user's requested write), and growth
is bounded to the most recent `_KEEP_PER_PATH` snapshots per original path.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from halia.store.database import DB_PATH, connect

SNAPSHOTS_DIR = Path.home() / ".halia" / "snapshots"

# Cap snapshots retained per original path (unattended overwrites would grow forever).
_KEEP_PER_PATH = 10


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _resolve(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve())


def snapshot_file(path: Path, db_path: Path | None = None) -> Path | None:
    """Copy `path`'s current bytes into the snapshot store and index them.

    Returns the snapshot file path, or None if there was nothing to snapshot (the
    file doesn't exist yet) or the backup couldn't be written. Never raises.
    """
    db_path = db_path or DB_PATH  # resolved at call time so tests can redirect it
    try:
        if not path.is_file():
            return None
        SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        data = path.read_bytes()
        stored_name = f"{uuid.uuid4().hex}{path.suffix}"
        dest = SNAPSHOTS_DIR / stored_name
        dest.write_bytes(data)
        original = _resolve(path)
        conn = connect(db_path)
        try:
            conn.execute(
                "INSERT INTO snapshots (id, created_at, original_path, stored_name, size_bytes) "
                "VALUES (?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, _now(), original, stored_name, len(data)),
            )
            conn.commit()
            _prune(conn, original)
            conn.commit()
        finally:
            conn.close()
        return dest
    except OSError:
        return None


def _prune(conn: sqlite3.Connection, original_path: str) -> None:
    """Drop snapshots for `original_path` beyond the newest `_KEEP_PER_PATH`."""
    stale = conn.execute(
        "SELECT id, stored_name FROM snapshots WHERE original_path = ? "
        "ORDER BY created_at DESC, rowid DESC LIMIT -1 OFFSET ?",
        (original_path, _KEEP_PER_PATH),
    ).fetchall()
    for sid, stored_name in stale:
        (SNAPSHOTS_DIR / stored_name).unlink(missing_ok=True)
        conn.execute("DELETE FROM snapshots WHERE id = ?", (sid,))


def latest_snapshot(
    path: str | None = None, db_path: Path | None = None
) -> tuple[str, str, str, str, int] | None:
    """Return (id, original_path, stored_name, created_at, size_bytes) of the most
    recent snapshot — for `path` if given, else across all paths. None if empty."""
    conn = connect(db_path or DB_PATH)
    try:
        cols = "id, original_path, stored_name, created_at, size_bytes"
        if path:
            row = conn.execute(
                f"SELECT {cols} FROM snapshots WHERE original_path = ? "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (_resolve(path),),
            ).fetchone()
        else:
            row = conn.execute(
                f"SELECT {cols} FROM snapshots ORDER BY created_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
        return tuple(row) if row is not None else None
    finally:
        conn.close()


def restore_latest(path: str | None = None, db_path: Path | None = None) -> tuple[str, int] | None:
    """Restore the most recent snapshot (optionally for a specific path) and remove it.

    Returns (restored_path, bytes_restored), or None if there's nothing to undo.
    """
    row = latest_snapshot(path, db_path)
    if row is None:
        return None
    snap_id, original_path, stored_name, _created, _size = row
    src = SNAPSHOTS_DIR / stored_name
    if not src.is_file():
        _delete(snap_id, db_path)  # dangling index row — clean it up
        return None
    data = src.read_bytes()
    dest = Path(original_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    src.unlink(missing_ok=True)
    _delete(snap_id, db_path)
    return (original_path, len(data))


def list_snapshots(
    path: str | None = None, limit: int = 20, db_path: Path | None = None
) -> list[tuple[str, str, int]]:
    """Return [(original_path, created_at, size_bytes)] newest first."""
    conn = connect(db_path or DB_PATH)
    try:
        cols = "original_path, created_at, size_bytes"
        if path:
            rows = conn.execute(
                f"SELECT {cols} FROM snapshots WHERE original_path = ? "
                "ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (_resolve(path), limit),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT {cols} FROM snapshots ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]
    finally:
        conn.close()


def _delete(snap_id: str, db_path: Path | None = None) -> None:
    conn = connect(db_path or DB_PATH)
    try:
        conn.execute("DELETE FROM snapshots WHERE id = ?", (snap_id,))
        conn.commit()
    finally:
        conn.close()
