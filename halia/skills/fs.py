"""Read-only filesystem skills.

Safe (non-mutating) by design, but still routed through the permission floor so
they can't read secrets. These prove the tool-calling loop before any dangerous
capability (run_command) — which will require an approval gate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from halia.permissions.guard import check_readable, check_writable

_MAX_CHARS = 10_000


class ReadFile:
    name = "read_file"
    description = "Read a UTF-8 text file and return its contents (truncated to a safe size)."
    dangerous = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"path": {"type": "string", "description": "Path to the file to read."}},
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
        if len(text) > _MAX_CHARS:
            text = text[:_MAX_CHARS] + f"\n… [truncated at {_MAX_CHARS} chars]"
        return text


class WriteFile:
    name = "write_file"
    description = "Write text content to a file, creating it or OVERWRITING it if it exists."
    dangerous = True
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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return f"wrote {len(content)} chars to {path}"


class ListFiles:
    name = "list_files"
    description = "List the entries (files and directories) in a directory."
    dangerous = False
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
