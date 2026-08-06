"""Image storage and retrieval for vision-capable models.

Stores images in ~/.halia/images/ with content-hash dedup. Each image is
tracked in the SQLite `images` table with metadata (dimensions, mime type,
original path). The stored file is named <hash>.<ext> for deduplication.

Usage:
    from halia.images import store_image, get_image, list_images
    img = store_image("~/Photos/screenshot.png")
    # img.id can be referenced in conversations
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from halia.config.settings import CONFIG_DIR
from halia.store.database import DB_PATH, connect

IMAGES_DIR = CONFIG_DIR / "images"

# Supported image extensions
_SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

# Limits
_MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10MB per image
_MAX_TOTAL_BYTES = 50 * 1024 * 1024  # 50MB total storage


@dataclass(frozen=True)
class Image:
    """A stored image with metadata."""

    id: str
    stored_at: str
    original_path: str
    filename: str
    mime_type: str
    width: int
    height: int
    size_bytes: int
    content_hash: str


def _ensure_dir() -> None:
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)


def _content_hash(data: bytes) -> str:
    """SHA-256 hash of the file content."""
    return hashlib.sha256(data).hexdigest()[:16]


def _mime_from_ext(ext: str) -> str:
    """Map file extension to MIME type."""
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(ext.lower(), "image/png")


def _get_image_size(data: bytes, ext: str) -> tuple[int, int]:
    """Get image dimensions without PIL — parse the header bytes."""
    if ext in (".png",):
        # PNG: width at offset 16, height at offset 20 (4 bytes each, big-endian)
        if len(data) >= 24:
            w = int.from_bytes(data[16:20], "big")
            h = int.from_bytes(data[20:24], "big")
            return w, h
    elif ext in (".jpg", ".jpeg"):
        # JPEG: need to parse markers — simplified detection
        # Returns 0,0 if parsing fails (non-critical)
        try:
            i = 0
            while i < len(data) - 1:
                if data[i] != 0xFF:
                    break
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2):
                    if i + 9 < len(data):
                        h = int.from_bytes(data[i + 5 : i + 7], "big")
                        w = int.from_bytes(data[i + 7 : i + 9], "big")
                        return w, h
                if marker == 0xD9:
                    break
                if marker in (0xD8, 0xD9, 0x00):
                    i += 2
                    continue
                if i + 3 < len(data):
                    seg_len = int.from_bytes(data[i + 2 : i + 4], "big")
                    i += 2 + seg_len
                else:
                    break
        except (IndexError, ValueError):
            pass
    elif ext in (".gif",):
        # GIF: width at offset 6, height at offset 8
        if len(data) >= 10:
            w = int.from_bytes(data[6:8], "little")
            h = int.from_bytes(data[8:10], "little")
            return w, h
    elif ext in (".webp",):
        # WebP: RIFF header, width/height in VP8 chunk
        if len(data) >= 30 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            w = int.from_bytes(data[26:28], "little") & 0x3FFF
            h = int.from_bytes(data[28:30], "little") & 0x3FFF
            return w, h
    return 0, 0


def store_image(path: str, db_path: Path = DB_PATH) -> Image:
    """Store an image file, deduplicating by content hash.

    Args:
        path: Path to the image file (supports ~ expansion).
        db_path: Path to the SQLite database.

    Returns:
        Image metadata.

    Raises:
        FileNotFoundError: If the file doesn't exist.
        ValueError: If the file is too large or unsupported format.
    """
    src = Path(path).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(f"image not found: {src}")

    ext = src.suffix.lower()
    if ext not in _SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"unsupported format '{ext}' — supported: {', '.join(sorted(_SUPPORTED_EXTENSIONS))}"
        )

    data = src.read_bytes()
    if len(data) > _MAX_SIZE_BYTES:
        raise ValueError(f"image too large: {len(data) / 1024 / 1024:.1f}MB (max 10MB)")

    content_hash = _content_hash(data)
    _ensure_dir()

    # Check if we already have this image (dedup)
    conn = connect(db_path)
    try:
        existing = conn.execute(
            "SELECT id, stored_at, original_path, filename, mime_type, "
            "width, height, size_bytes, content_hash FROM images WHERE content_hash = ?",
            (content_hash,),
        ).fetchone()
        if existing:
            return Image(
                id=existing[0], stored_at=existing[1], original_path=existing[2],
                filename=existing[3], mime_type=existing[4], width=existing[5],
                height=existing[6], size_bytes=existing[7], content_hash=existing[8],
            )

        # Store the file
        stored_filename = f"{content_hash}{ext}"
        dest = IMAGES_DIR / stored_filename
        dest.write_bytes(data)

        # Get dimensions
        w, h = _get_image_size(data, ext)
        mime = _mime_from_ext(ext)

        # Insert metadata
        img_id = uuid.uuid4().hex[:8]
        now = datetime.now(UTC).isoformat()
        conn.execute(
            "INSERT INTO images (id, stored_at, original_path, filename, mime_type, "
            "width, height, size_bytes, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (img_id, now, str(src), stored_filename, mime, w, h, len(data), content_hash),
        )
        conn.commit()

        return Image(
            id=img_id, stored_at=now, original_path=str(src),
            filename=stored_filename, mime_type=mime, width=w, height=h,
            size_bytes=len(data), content_hash=content_hash,
        )
    finally:
        conn.close()


def get_image(image_id: str, db_path: Path = DB_PATH) -> Image | None:
    """Retrieve image metadata by ID."""
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT id, stored_at, original_path, filename, mime_type, "
            "width, height, size_bytes, content_hash FROM images WHERE id = ?",
            (image_id,),
        ).fetchone()
        if not row:
            return None
        return Image(
            id=row[0], stored_at=row[1], original_path=row[2],
            filename=row[3], mime_type=row[4], width=row[5],
            height=row[6], size_bytes=row[7], content_hash=row[8],
        )
    finally:
        conn.close()


def get_image_path(image_id: str, db_path: Path = DB_PATH) -> Path | None:
    """Get the filesystem path to a stored image."""
    img = get_image(image_id, db_path)
    if img is None:
        return None
    path = IMAGES_DIR / img.filename
    return path if path.exists() else None


def list_images(limit: int = 20, db_path: Path = DB_PATH) -> list[Image]:
    """List recently stored images."""
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, stored_at, original_path, filename, mime_type, "
            "width, height, size_bytes, content_hash FROM images "
            "ORDER BY stored_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            Image(
                id=r[0], stored_at=r[1], original_path=r[2],
                filename=r[3], mime_type=r[4], width=r[5],
                height=r[6], size_bytes=r[7], content_hash=r[8],
            )
            for r in rows
        ]
    finally:
        conn.close()


def delete_image(image_id: str, db_path: Path = DB_PATH) -> bool:
    """Delete an image by ID. Returns True if it existed."""
    img = get_image(image_id, db_path)
    if img is None:
        return False
    # Remove the file
    file_path = IMAGES_DIR / img.filename
    if file_path.exists():
        file_path.unlink()
    # Remove from DB
    conn = connect(db_path)
    try:
        conn.execute("DELETE FROM images WHERE id = ?", (image_id,))
        conn.commit()
    finally:
        conn.close()
    return True
