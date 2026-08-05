"""Code search — search_code (find references across a codebase).

A deterministic repo-wide grep: given a symbol or pattern, return every file:line it
appears on. This is the tool that turns "I assume this field drives the logic" into
"here is every place it is actually read" — e.g. discovering that a required, validated
DTO field is never consumed by any branch. It reads the files directly, so the answer is
ground truth, not a guess from a name.

Read-only and routed through the permission floor. Skips the usual noise (node_modules,
.git, build output) and binary files so a real repo stays searchable.
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path
from typing import Any

from halia.permissions.guard import check_readable

# Directories that are never source and would swamp the results (or the walk).
_SKIP_DIRS = frozenset(
    {
        ".git", "node_modules", "dist", "build", ".next", "out", "coverage",
        "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache", ".cache",
        "target", ".idea", ".gradle", "vendor", ".terraform",
    }
)
_MAX_FILE_BYTES = 2_000_000  # skip files larger than this (likely generated/minified)
_MAX_FILES = 8000  # a safety bound on how many files one search will scan
_DEFAULT_MAX_RESULTS = 200


def _is_binary(sample: bytes) -> bool:
    return b"\x00" in sample


class SearchCode:
    name = "search_code"
    description = (
        "Find every occurrence of a symbol or pattern across a codebase (like `grep -rn`). "
        "Given a `query` and a root `path` (a directory or a single file), returns each hit as "
        "file:line plus the matching line. Use it to confirm where a symbol is actually "
        "READ / WRITTEN / DEFINED before claiming it drives behaviour — e.g. whether a "
        "required, validated field is ever consumed by a branch, or to enumerate every place a "
        "route/outcome is produced. Deterministic (reads the files directly). Skips "
        "node_modules/.git/build output and binary files."
    )
    dangerous = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "query": {"type": "string", "description": "Text to find (literal unless regex=true)."},
            "path": {"type": "string", "description": "Directory or file to search (default '.')."},
            "regex": {
                "type": "boolean",
                "description": "Treat query as a regular expression (default false = literal).",
            },
            "ignore_case": {"type": "boolean", "description": "Case-insensitive (default false)."},
            "file_glob": {
                "type": "string",
                "description": "Limit to files matching this glob, e.g. '*.ts' (default: all).",
            },
            "max_results": {
                "type": "integer",
                "description": f"Cap on matches returned (default {_DEFAULT_MAX_RESULTS}).",
            },
        },
        "required": ["query"],
    }

    def run(self, args: dict[str, Any]) -> str:
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return "error: 'query' is required and must be a non-empty string"
        raw = args.get("path", ".")
        root = Path(raw if isinstance(raw, str) and raw.strip() else ".").expanduser()
        check_readable(root)
        if not root.exists():
            return f"error: path does not exist: {root}"

        flags = re.IGNORECASE if bool(args.get("ignore_case")) else 0
        pattern_src = query if bool(args.get("regex")) else re.escape(query)
        try:
            pattern = re.compile(pattern_src, flags)
        except re.error as exc:
            return f"error: invalid regex: {exc}"

        file_glob = args.get("file_glob")
        file_glob = file_glob if isinstance(file_glob, str) and file_glob.strip() else None
        max_results = args.get("max_results")
        limit = (
            max_results
            if isinstance(max_results, int) and max_results > 0
            else _DEFAULT_MAX_RESULTS
        )

        files = self._files(root, file_glob)
        by_file: dict[str, list[str]] = {}
        total = 0
        truncated = False
        for file in files:
            if total >= limit:
                truncated = True
                break
            hits = self._search_file(file, pattern, root, limit - total)
            if hits:
                by_file[hits[0][0]] = [line for _, line in hits]
                total += len(hits)

        if total == 0:
            where = "this file" if root.is_file() else str(root)
            return f"No matches for {query!r} in {where}."

        n_files = len(by_file)
        header = f"{total}{'+' if truncated else ''} match(es) in {n_files} file(s) for {query!r}:"
        blocks = [header]
        for rel, lines in by_file.items():
            blocks.append("\n" + rel)
            blocks.extend("  " + line for line in lines)
        if truncated:
            blocks.append(
                f"\n… stopped at {limit} matches — narrow the query, set a path, "
                "or raise max_results."
            )
        return "\n".join(blocks)

    def _files(self, root: Path, file_glob: str | None) -> list[Path]:
        if root.is_file():
            return [root]
        out: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
            for name in sorted(filenames):
                if file_glob and not fnmatch.fnmatch(name, file_glob):
                    continue
                out.append(Path(dirpath) / name)
                if len(out) >= _MAX_FILES:
                    return out
        return out

    def _search_file(
        self, file: Path, pattern: re.Pattern[str], root: Path, remaining: int
    ) -> list[tuple[str, str]]:
        try:
            if file.stat().st_size > _MAX_FILE_BYTES:
                return []
            data = file.read_bytes()
        except OSError:
            return []
        if _is_binary(data[:1024]):
            return []
        try:
            rel = str(file.relative_to(root)) if root.is_dir() else file.name
        except ValueError:
            rel = str(file)
        hits: list[tuple[str, str]] = []
        text = data.decode("utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                hits.append((rel, f"{rel}:{lineno}: {line.strip()[:200]}"))
                if len(hits) >= remaining:
                    break
        return hits
