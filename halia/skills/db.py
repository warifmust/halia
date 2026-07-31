"""Structured query skill — read-only SQL over a SQLite database.

Business data lives in databases; this lets the agent query it directly (agentic
search over structured data), with the DB engine doing the computation. Two
layers of safety: the query must be a SELECT/WITH, AND the connection is opened
read-only (`mode=ro`), so a write is rejected by SQLite itself even if the text
check were bypassed.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from halia.permissions.guard import check_readable

_MAX_ROWS = 100


class QueryDb:
    name = "query_db"
    description = (
        "Run a READ-ONLY SQL query (SELECT / WITH … SELECT only) against a SQLite "
        "database file and return the rows. Use SQL aggregates (SUM, COUNT, …) for "
        "exact computation over the data."
    )
    dangerous = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "path": {"type": "string", "description": "Path to the SQLite database file."},
            "query": {"type": "string", "description": "A read-only SQL SELECT query."},
        },
        "required": ["path", "query"],
    }

    def run(self, args: dict[str, Any]) -> str:
        raw_path = args.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return "error: 'path' is required and must be a non-empty string"
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return "error: 'query' is required and must be a non-empty string"

        statement = query.strip().rstrip(";").strip()
        if not statement.lower().startswith(("select", "with")):
            return "error: only read-only SELECT (or WITH … SELECT) queries are allowed"

        path = Path(raw_path).expanduser()
        check_readable(path)
        if not path.is_file():
            return f"error: not a file: {path}"

        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                cursor = conn.execute(statement)  # single statement; RO rejects writes
                columns = [d[0] for d in cursor.description] if cursor.description else []
                rows = cursor.fetchmany(_MAX_ROWS + 1)
            finally:
                conn.close()
        except sqlite3.Error as exc:
            return f"error running query: {exc}"

        if not columns:
            return "(query returned no columns)"

        truncated = len(rows) > _MAX_ROWS
        lines = [" | ".join(columns)]
        lines.extend(" | ".join(str(value) for value in row) for row in rows[:_MAX_ROWS])
        if truncated:
            lines.append(f"… [truncated at {_MAX_ROWS} rows]")
        return "\n".join(lines)
