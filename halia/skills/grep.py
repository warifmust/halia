"""grep_file — lightweight single-file text search.

Unlike search_code (which walks a repo), grep_file searches ONE file. Faster,
cheaper, and more precise when you already know which file to look in. Supports
regex, case-insensitive search, line numbers, and context lines.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from halia.permissions.guard import check_readable

_DEFAULT_MAX_LINES = 100
_CONTEXT_LINES = 0  # lines before/after each match


class GrepFile:
    name = "grep_file"
    description = (
        "Search for a pattern in a single file and return matching lines with line "
        "numbers. Supports regex, case-insensitive search, and context lines. "
        "Lighter than search_code when you already know which file to search."
    )
    dangerous = False
    untrusted = True  # reads user-supplied file content
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "path": {"type": "string", "description": "Path to the file to search."},
            "query": {"type": "string", "description": "Text or regex pattern to find."},
            "regex": {"type": "boolean", "description": "Treat query as regex (default false)."},
            "ignore_case": {"type": "boolean", "description": "Case-insensitive (default false)."},
            "context": {
                "type": "integer",
                "description": "Context lines before/after each match (default 0).",
            },
            "max_lines": {
                "type": "integer",
                "description": f"Max lines returned (default {_DEFAULT_MAX_LINES}).",
            },
        },
        "required": ["path", "query"],
    }

    def run(self, args: dict[str, Any]) -> str:
        raw_path = args.get("path")
        query = args.get("query")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return "error: 'path' is required and must be a non-empty string"
        if not isinstance(query, str) or not query.strip():
            return "error: 'query' is required and must be a non-empty string"

        path = Path(raw_path).expanduser()
        check_readable(path)
        if not path.is_file():
            return f"error: not a file: {path}"

        flags = re.IGNORECASE if bool(args.get("ignore_case")) else 0
        pattern_src = query if bool(args.get("regex")) else re.escape(query)
        try:
            pattern = re.compile(pattern_src, flags)
        except re.error as exc:
            return f"error: invalid regex: {exc}"

        context = args.get("context", _CONTEXT_LINES)
        if not isinstance(context, int) or context < 0:
            context = 0
        max_lines = args.get("max_lines", _DEFAULT_MAX_LINES)
        if not isinstance(max_lines, int) or max_lines <= 0:
            max_lines = _DEFAULT_MAX_LINES

        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            return f"error reading {path}: {exc}"

        matches: list[tuple[int, str]] = []
        for i, line in enumerate(lines):
            if pattern.search(line):
                matches.append((i + 1, line))  # 1-indexed

        if not matches:
            return f"no matches for '{query}' in {path.name}"

        # Apply context: collect surrounding lines
        result_lines: list[str] = []
        shown: set[int] = set()
        for line_num, _line in matches:
            start = max(0, line_num - 1 - context)  # 0-indexed
            end = min(len(lines), line_num + context)  # 1-indexed exclusive
            for idx in range(start, end):
                if idx in shown:
                    continue
                shown.add(idx)
                ln = idx + 1
                marker = ":" if ln == line_num else "-"
                result_lines.append(f"{ln}{marker}{lines[idx]}")
            if len(result_lines) >= max_lines:
                break

        if len(result_lines) > max_lines:
            result_lines = result_lines[:max_lines]
            result_lines.append(f"… ({len(matches)} matches, truncated at {max_lines} lines)")

        header = f"{len(matches)} match(es) in {path.name}"
        return header + "\n" + "\n".join(result_lines)
