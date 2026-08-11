"""Read-only filesystem skills.

Safe (non-mutating) by design, but still routed through the permission floor so
they can't read secrets. These prove the tool-calling loop before any dangerous
capability (run_command) — which will require an approval gate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from halia.permissions.guard import check_readable, check_writable
from halia.store.snapshots import snapshot_file

# Per-read char cap. Kept UNDER the 30k quarantine truncation so read_file's own paging note
# (not the generic quarantine one) is what the model sees when a file is larger.
_MAX_CHARS = 25_000


def _coerce_int(value: Any, default: int | None, minimum: int) -> int | None:
    """Accept an int or a numeric string (models often pass numbers as strings)."""
    if value is None:
        return default
    try:
        return max(minimum, int(value))
    except (TypeError, ValueError):
        return default


class ReadFile:
    name = "read_file"
    description = (
        "Read a UTF-8 text file. Returns the whole file when it fits; for a large file use "
        "`offset` (1-indexed line to start from) and `limit` (max lines) to PAGE through it "
        "instead of re-reading — the result footer tells you the next offset."
    )
    dangerous = False
    untrusted = True
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "path": {"type": "string", "description": "Path to the file to read."},
            "offset": {
                "type": "integer",
                "description": "1-indexed line to start from (default 1).",
            },
            "limit": {
                "type": "integer",
                "description": "Max lines to return (default: to end of file, capped by size).",
            },
        },
        "required": ["path"],
    }

    def run(self, args: dict[str, Any]) -> str:
        raw = args.get("path")
        if not isinstance(raw, str) or not raw.strip():
            return "error: 'path' is required and must be a non-empty string"
        path = Path(raw).expanduser()
        check_readable(path)  # raises PermissionDenied for sensitive paths
        if not path.is_file():
            return f"error: not a file: {path}"
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines(keepends=True)  # keepends → rejoined slice is byte-exact
        total = len(lines)

        offset = _coerce_int(args.get("offset"), default=1, minimum=1) or 1
        limit = _coerce_int(args.get("limit"), default=None, minimum=1)
        start = offset - 1
        if total and start >= total:
            return f"error: offset {offset} is past end of file ({total} lines)"
        end = total if limit is None else min(total, start + limit)
        body = "".join(lines[start:end])

        # Char safety cap on the returned window; recompute where it actually stopped so the
        # "continue" offset is correct.
        truncated_chars = len(body) > _MAX_CHARS
        if truncated_chars:
            body = body[:_MAX_CHARS]
            end = start + max(1, body.count("\n"))

        notes: list[str] = []
        if truncated_chars:
            notes.append(f"truncated at {_MAX_CHARS} chars")
        if start > 0 or end < total:
            notes.append(f"lines {start + 1}–{end} of {total}")
        if end < total:
            notes.append(f"pass offset={end + 1} to continue")
        if notes:
            body += f"\n… [{'; '.join(notes)}]"
        return body


class WriteFile:
    name = "write_file"
    description = "Write text content to a file, creating it or OVERWRITING it if it exists."
    dangerous = True
    untrusted = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "path": {"type": "string", "description": "Path to the file to write."},
            "content": {"type": "string", "description": "The text content to write."},
        },
        "required": ["path", "content"],
    }

    def run(self, args: dict[str, Any]) -> str:
        raw = args.get("path")
        if not isinstance(raw, str) or not raw.strip():
            return "error: 'path' is required and must be a non-empty string"
        content = args.get("content")
        if not isinstance(content, str):
            return "error: 'content' is required and must be a string"
        path = Path(raw).expanduser()
        check_writable(path)  # raises PermissionDenied for sensitive paths
        # Snapshot the current version before overwriting, so `halia undo` can restore it.
        snapped = snapshot_file(path) if path.is_file() else None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        msg = f"wrote {len(content)} chars to {path}"
        if snapped is not None:
            msg += " (previous version saved — run `halia undo` to restore)"
        return msg


class ListFiles:
    name = "list_files"
    description = "List the entries (files and directories) in a directory."
    dangerous = False
    untrusted = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "path": {"type": "string", "description": "Directory to list (default '.')."}
        },
        "required": [],
    }

    def run(self, args: dict[str, Any]) -> str:
        raw = args.get("path", ".")
        path = Path(raw if isinstance(raw, str) and raw.strip() else ".").expanduser()
        check_readable(path)
        if not path.is_dir():
            return f"error: not a directory: {path}"
        entries = sorted(
            f"{p.name}/" if p.is_dir() else p.name for p in path.iterdir()
        )
        if not entries:
            return "(empty directory)"
        return "\n".join(entries)
