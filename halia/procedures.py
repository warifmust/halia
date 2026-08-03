"""Test procedures — reusable, taught test templates that halia remembers.

Different QA/eng teams test in different ways, so halia lets a user TEACH a test
procedure once and reuse it. A procedure is a *declarative template*, not a workflow
engine: it captures WHAT is under test, the test-DATA spec, the ACTION (an endpoint
to call), and how to judge the RESULT — then the ordinary fixed loop + skills
(`http_request`, `calculate`, `make_excel`) execute it. Storing it (SQLite, like
`profiles`) is the "remember" half; `to_prompt` injects it as run guidance.

Trust discipline is baked into the rendered instructions: the pass/fail verdict must
come from the actual `http_request` response (a deterministic compare), never a guess.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from halia.store.database import DB_PATH, connect

# Slots that must be filled before a procedure can run. These drive the teach-time
# elicitation (ask only for a MISSING required slot, once, then persist) — see the
# interaction layer. `name` is always required and handled separately.
_REQUIRED = ("target", "data_spec", "url", "result_columns", "pass_rule")


@dataclass(frozen=True)
class Procedure:
    """A taught, reusable test template."""

    name: str
    description: str = ""
    target: str = ""  # what is under test, e.g. "POST /auth/login"
    data_spec: str = ""  # how to get test data (synthesize per schema, or "user provides")
    method: str = "GET"  # the action: HTTP method …
    url: str = ""  # … and endpoint (may contain {placeholders} filled at run time)
    headers: dict[str, str] = field(default_factory=dict)  # default request headers
    result_columns: list[str] = field(default_factory=list)  # output CSV schema
    pass_rule: str = ""  # deterministic verdict rule, e.g. "actual_status == expected_status"

    def missing_slots(self) -> list[str]:
        """Required slots that are still empty (drives teach-time elicitation)."""
        missing: list[str] = []
        for slot in _REQUIRED:
            value = getattr(self, slot)
            if not value:  # "" or [] or {}
                missing.append(slot)
        return missing

    def is_runnable(self) -> bool:
        """True once every required slot is filled."""
        return not self.missing_slots()

    def to_prompt(self) -> str:
        """Render the procedure as instructions injected into the run's context."""
        lines = [f"TEST PROCEDURE: {self.name}"]
        if self.description:
            lines.append(self.description)
        lines.append("")
        lines.append(f"What is under test: {self.target or '(unspecified)'}")
        lines.append(f"Test data: {self.data_spec or '(unspecified)'}")
        action = f"Action: {self.method} {self.url}".rstrip()
        lines.append(action)
        if self.headers:
            rendered = ", ".join(f"{k}: {v}" for k, v in self.headers.items())
            lines.append(f"  request headers: {rendered}")
        lines.append(f"Pass/fail rule: {self.pass_rule or '(unspecified)'}")
        if self.result_columns:
            cols = ", ".join(self.result_columns)
            lines.append(f"Output CSV columns (use EXACTLY these): {cols}")
        lines.append("")
        lines.append("Execute this procedure:")
        lines.append(
            "1. Prepare the test data as described above — synthesize it (then write_file), "
            "or use data the user supplies. For gated/real data (e.g. live accounts), ask "
            "the user to provide it; do not invent it."
        )
        lines.append(
            f"2. For each case, call the endpoint with http_request ({self.method} {self.url}). "
            "The HTTP status/response is the grounded result."
        )
        lines.append(
            "3. Decide pass/fail with the rule above, DETERMINISTICALLY — compare the actual "
            "response to the expected value exactly (use calculate for any arithmetic). Never "
            "guess a verdict; it must trace to the http_request response."
        )
        if self.result_columns:
            lines.append(
                f"4. Write results to a CSV with exactly these columns: "
                f"{', '.join(self.result_columns)}."
            )
        lines.append(
            "5. Report a summary: N passed / M failed, and cite the results file. Flag any "
            "case whose verdict you could not ground."
        )
        return "\n".join(lines)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def save_procedure(procedure: Procedure, db_path: Path = DB_PATH) -> None:
    """Create or replace a procedure (preserving created_at on replace)."""
    conn = connect(db_path)
    try:
        existing = conn.execute(
            "SELECT created_at FROM procedures WHERE name = ?", (procedure.name,)
        ).fetchone()
        created_at = existing[0] if existing else _now()
        conn.execute(
            "INSERT OR REPLACE INTO procedures (name, description, target, data_spec, method, "
            "url, headers_json, result_columns_json, pass_rule, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                procedure.name,
                procedure.description,
                procedure.target,
                procedure.data_spec,
                procedure.method,
                procedure.url,
                json.dumps(procedure.headers),
                json.dumps(procedure.result_columns),
                procedure.pass_rule,
                created_at,
                _now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _row_to_procedure(row: tuple[Any, ...]) -> Procedure:
    headers: Any = json.loads(row[6])
    columns: Any = json.loads(row[7])
    return Procedure(
        name=row[0],
        description=row[1],
        target=row[2],
        data_spec=row[3],
        method=row[4],
        url=row[5],
        headers=dict(headers),
        result_columns=list(columns),
        pass_rule=row[8],
    )


_COLUMNS = (
    "name, description, target, data_spec, method, url, headers_json, "
    "result_columns_json, pass_rule"
)


def get_procedure(name: str, db_path: Path = DB_PATH) -> Procedure | None:
    """Load a procedure by name, or None."""
    if not db_path.exists():
        return None
    conn = connect(db_path)
    try:
        row = conn.execute(
            f"SELECT {_COLUMNS} FROM procedures WHERE name = ?", (name,)
        ).fetchone()
    finally:
        conn.close()
    return _row_to_procedure(row) if row is not None else None


def list_procedures(db_path: Path = DB_PATH) -> list[Procedure]:
    """All procedures, by name."""
    if not db_path.exists():
        return []
    conn = connect(db_path)
    try:
        rows = conn.execute(f"SELECT {_COLUMNS} FROM procedures ORDER BY name").fetchall()
    finally:
        conn.close()
    return [_row_to_procedure(row) for row in rows]


def delete_procedure(name: str, db_path: Path = DB_PATH) -> bool:
    """Delete a procedure; True if it existed."""
    conn = connect(db_path)
    try:
        cursor = conn.execute("DELETE FROM procedures WHERE name = ?", (name,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
