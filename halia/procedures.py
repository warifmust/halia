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
# Always-required slots. The ACTION requirement (a url OR steps) is checked separately,
# so a procedure can be a single endpoint call OR a multi-step "first do X, then Y" flow.
_REQUIRED_CORE = ("target", "data_spec", "result_columns", "pass_rule")


@dataclass(frozen=True)
class Procedure:
    """A taught, reusable test template."""

    name: str
    description: str = ""
    target: str = ""  # what is under test, e.g. "POST /auth/login"
    data_spec: str = ""  # how to get test data (what rows/shape)
    data_source: str = "synthesize"  # "synthesize" (halia generates) | "provided" (user supplies)
    steps: list[str] = field(default_factory=list)  # ordered "first do X, then Y" instructions
    method: str = "GET"  # the action: HTTP method …
    url: str = ""  # … and endpoint (may contain {placeholders} filled at run time)
    headers: dict[str, str] = field(default_factory=dict)  # default request headers
    result_columns: list[str] = field(default_factory=list)  # output CSV schema
    pass_rule: str = ""  # deterministic verdict rule, e.g. "actual_status == expected_status"

    def missing_slots(self) -> list[str]:
        """Required slots that are still empty (drives teach-time elicitation)."""
        missing: list[str] = []
        for slot in _REQUIRED_CORE:
            if not getattr(self, slot):  # "" or [] or {}
                missing.append(slot)
        if not self.url and not self.steps:
            missing.append("action (a url or steps)")
        return missing

    def is_runnable(self) -> bool:
        """True once every required slot is filled."""
        return not self.missing_slots()

    def provides_own_data(self) -> bool:
        """True if the user supplies the test data (gated/real) rather than halia synthesizing."""
        return self.data_source == "provided"

    def to_prompt(self) -> str:
        """Render the procedure as instructions injected into the run's context."""
        lines = [f"TEST PROCEDURE: {self.name}"]
        if self.description:
            lines.append(self.description)
        lines.append("")
        lines.append(f"What is under test: {self.target or '(unspecified)'}")
        source = "provided by the user" if self.provides_own_data() else "synthesized by halia"
        lines.append(f"Test data ({source}): {self.data_spec or '(unspecified)'}")
        if self.steps:
            lines.append("Steps (in order):")
            for i, step in enumerate(self.steps, 1):
                lines.append(f"  {i}. {step}")
        if self.url:
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
        if self.provides_own_data():
            lines.append(
                "1. The test data is PROVIDED BY THE USER (gated/real, e.g. live accounts). "
                "Use ONLY the data the user supplies — read the file they name (read_csv / "
                "read_file) or the rows they paste. Do NOT invent or synthesize rows. If no "
                "data was provided, ask the user for it before continuing."
            )
        else:
            lines.append(
                "1. Synthesize the test data described above, then write_file it to a CSV so "
                "it's inspectable. Realistic but non-sensitive values are fine."
            )
        if self.url:
            lines.append(
                f"2. For each case, call the endpoint with http_request ({self.method} "
                f"{self.url}). The HTTP status/response is the grounded result."
            )
        else:
            lines.append(
                "2. Carry out the steps above for each case, using tools for anything "
                "measurable. Any value you will judge on must come from a tool result."
            )
        lines.append(
            "3. Decide pass/fail with the rule above by calling check_expectation (actual value "
            "vs expected) — it returns a deterministic PASS/FAIL that lands in the audit trail. "
            "Never guess a verdict; it must trace to a tool result."
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
            "INSERT OR REPLACE INTO procedures (name, description, target, data_spec, "
            "data_source, steps_json, method, url, headers_json, result_columns_json, "
            "pass_rule, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                procedure.name,
                procedure.description,
                procedure.target,
                procedure.data_spec,
                procedure.data_source,
                json.dumps(procedure.steps),
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
    steps: Any = json.loads(row[5])
    headers: Any = json.loads(row[8])
    columns: Any = json.loads(row[9])
    return Procedure(
        name=row[0],
        description=row[1],
        target=row[2],
        data_spec=row[3],
        data_source=row[4],
        steps=list(steps),
        method=row[6],
        url=row[7],
        headers=dict(headers),
        result_columns=list(columns),
        pass_rule=row[10],
    )


_COLUMNS = (
    "name, description, target, data_spec, data_source, steps_json, method, url, "
    "headers_json, result_columns_json, pass_rule"
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
