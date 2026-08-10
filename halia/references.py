"""Reference files — taught formats and documents.

Users can teach halia specific formats, templates, or documents via `/teach`.
These files are stored in ~/.halia/files/ and tracked in the `ref_files` table.
The model calls `learn_from_reference` to read them before starting work.

Two use cases:
- `/teach` — store with a profile tag (qa, finance, etc.) → model follows the format
- `/files` — store for later retrieval (searchable, no profile tag)
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from halia.config.settings import CONFIG_DIR
from halia.store.database import DB_PATH, connect

FILES_DIR = CONFIG_DIR / "files"

# Supported file types for teaching
_SUPPORTED_TYPES = {
    ".pdf", ".docx", ".txt", ".md", ".csv", ".json", ".xlsx",
    ".html", ".xml", ".yaml", ".yml", ".toml",
}

_MAX_SIZE_BYTES = 5 * 1024 * 1024  # 5MB per file
_URL_MAX_CHARS = 50_000  # how much page text to capture when teaching a URL


@dataclass(frozen=True)
class Reference:
    """A stored reference file with metadata."""

    id: str
    stored_at: str
    original_path: str
    filename: str
    file_type: str
    profile: str
    size_bytes: int
    description: str
    url: str = ""  # set for references taught from a web URL (empty for file-based)


def _ensure_dir() -> None:
    FILES_DIR.mkdir(parents=True, exist_ok=True)


def _content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def store_reference(
    path: str,
    profile: str = "",
    description: str = "",
    db_path: Path = DB_PATH,
) -> Reference:
    """Store a reference file. Returns Reference metadata.

    Args:
        path: Path to the file to store.
        profile: Optional profile tag (qa, finance, etc.) — empty means generic.
        description: Optional description of what this file teaches.
    """
    src = Path(path).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(f"file not found: {src}")

    ext = src.suffix.lower()
    if ext not in _SUPPORTED_TYPES:
        raise ValueError(
            f"unsupported format '{ext}' — supported: {', '.join(sorted(_SUPPORTED_TYPES))}"
        )

    data = src.read_bytes()
    if len(data) > _MAX_SIZE_BYTES:
        raise ValueError(f"file too large: {len(data) / 1024:.0f}KB (max 5MB)")

    content_hash = _content_hash(data)
    _ensure_dir()

    # Copy file to storage
    stored_filename = f"{content_hash}{ext}"
    dest = FILES_DIR / stored_filename
    dest.write_bytes(data)

    # Insert metadata
    ref_id = uuid.uuid4().hex[:8]
    now = datetime.now(UTC).isoformat()
    conn = connect(db_path)
    try:
        conn.execute(
            "INSERT INTO ref_files (id, stored_at, original_path, filename, "
            "stored_filename, file_type, profile, size_bytes, description, url) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ref_id, now, str(src), src.name, stored_filename,
                ext, profile, len(data), description, "",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return Reference(
        id=ref_id, stored_at=now, original_path=str(src),
        filename=src.name, file_type=ext, profile=profile,
        size_bytes=len(data), description=description,
    )


def store_url_reference(
    url: str,
    profile: str = "",
    description: str = "",
    db_path: Path = DB_PATH,
    fetcher: Callable[[str], str] | None = None,
) -> Reference:
    """Fetch a web URL's readable text and store it as a (profile-scoped) reference.

    `fetcher(url) -> text` is injectable for tests; by default it uses the shared
    `fetch_url_text` (which enforces the SSRF egress floor). Raises on fetch failure
    (EgressDenied / ValueError / httpx.HTTPError) so the caller can surface it.
    """
    from urllib.parse import urlparse

    if fetcher is None:
        from functools import partial

        from halia.skills.web import fetch_url_text

        fetcher = partial(fetch_url_text, max_chars=_URL_MAX_CHARS)

    text = fetcher(url)
    if not text.strip():
        raise ValueError(f"no readable text fetched from {url}")
    data = text.encode("utf-8")[:_MAX_SIZE_BYTES]

    content_hash = _content_hash(data)
    _ensure_dir()
    stored_filename = f"{content_hash}.md"
    (FILES_DIR / stored_filename).write_bytes(data)

    parsed = urlparse(url)
    label = (parsed.netloc + parsed.path).rstrip("/") or url
    if len(label) > 80:
        label = label[:79] + "…"

    ref_id = uuid.uuid4().hex[:8]
    now = datetime.now(UTC).isoformat()
    conn = connect(db_path)
    try:
        conn.execute(
            "INSERT INTO ref_files (id, stored_at, original_path, filename, "
            "stored_filename, file_type, profile, size_bytes, description, url) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ref_id, now, url, label, stored_filename, ".md", profile, len(data), description, url),
        )
        conn.commit()
    finally:
        conn.close()

    return Reference(
        id=ref_id, stored_at=now, original_path=url, filename=label,
        file_type=".md", profile=profile, size_bytes=len(data),
        description=description, url=url,
    )


def list_ref_files(
    profile: str | None = None,
    db_path: Path = DB_PATH,
) -> list[Reference]:
    """List stored ref_files, optionally filtered by profile."""
    _cols = (
        "id, stored_at, original_path, filename, file_type, "
        "profile, size_bytes, description, url"
    )
    conn = connect(db_path)
    try:
        if profile is not None:
            rows = conn.execute(
                f"SELECT {_cols} FROM ref_files "
                "WHERE profile = ? OR profile = '' ORDER BY stored_at DESC",
                (profile,),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT {_cols} FROM ref_files ORDER BY stored_at DESC"
            ).fetchall()
        return [
            Reference(
                id=r[0], stored_at=r[1], original_path=r[2],
                filename=r[3], file_type=r[4], profile=r[5],
                size_bytes=r[6], description=r[7], url=r[8],
            )
            for r in rows
        ]
    finally:
        conn.close()


def search_ref_files(query: str, db_path: Path = DB_PATH) -> list[Reference]:
    """Search ref_files by filename or description."""
    _cols = (
        "id, stored_at, original_path, filename, file_type, "
        "profile, size_bytes, description, url"
    )
    conn = connect(db_path)
    try:
        like = f"%{query}%"
        rows = conn.execute(
            f"SELECT {_cols} FROM ref_files "
            "WHERE filename LIKE ? OR description LIKE ? OR original_path LIKE ? "
            "ORDER BY stored_at DESC",
            (like, like, like),
        ).fetchall()
        return [
            Reference(
                id=r[0], stored_at=r[1], original_path=r[2],
                filename=r[3], file_type=r[4], profile=r[5],
                size_bytes=r[6], description=r[7], url=r[8],
            )
            for r in rows
        ]
    finally:
        conn.close()


def get_reference_path(ref_id: str, db_path: Path = DB_PATH) -> Path | None:
    """Get the filesystem path to a stored reference."""
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT stored_filename FROM ref_files WHERE id = ?", (ref_id,)
        ).fetchone()
        if not row:
            return None
        path = FILES_DIR / row[0]
        return path if path.exists() else None
    finally:
        conn.close()


def delete_reference(ref_id: str, db_path: Path = DB_PATH) -> bool:
    """Delete a reference by ID. Returns True if it existed."""
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT stored_filename FROM ref_files WHERE id = ?", (ref_id,)
        ).fetchone()
        if not row:
            return False
        file_path = FILES_DIR / row[0]
        if file_path.exists():
            file_path.unlink()
        conn.execute("DELETE FROM ref_files WHERE id = ?", (ref_id,))
        conn.commit()
        return True
    finally:
        conn.close()
