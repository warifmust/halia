"""Durable audit trail — persist each run's provenance to the SQLite store.

A trust product keeps the receipts. Every `halia run` is written to the `runs`
table in `~/.halia/halia.db` so it can be reviewed, reproduced, and (later)
verified against — the audit layer maturing from "shown once" to "recorded".

`~/.halia` is inside the permission floor, so the agent's own read_file/query_db
can't read the audit trail — the harness records it; the model can't rummage.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from halia.audit.trace import Step
from halia.store.database import DB_PATH, connect


@dataclass(frozen=True)
class RunRecord:
    """The persisted record of one run — including the trust receipts (plan + conscience)."""

    id: str
    started_at: str  # ISO 8601, UTC
    provider: str
    model: str
    prompt: str
    answer: str
    steps: list[Step]
    plan: str = ""  # the up-front plan, if planning was on
    unverified: list[str] = field(default_factory=list)  # figures no tool produced
    corrections: int = 0  # conscience self-heal passes triggered


def new_record(
    provider: str,
    model: str,
    prompt: str,
    answer: str,
    steps: list[Step],
    plan: str = "",
    unverified: list[str] | None = None,
    corrections: int = 0,
) -> RunRecord:
    """Build a RunRecord with a fresh id + timestamp."""
    return RunRecord(
        id=uuid.uuid4().hex[:12],
        started_at=datetime.now(UTC).isoformat(),
        provider=provider,
        model=model,
        prompt=prompt,
        answer=answer,
        steps=list(steps),
        plan=plan,
        unverified=list(unverified) if unverified is not None else [],
        corrections=corrections,
    )


def save_run(record: RunRecord, db_path: Path = DB_PATH) -> None:
    """Persist a run record to the database."""
    steps_json = json.dumps([asdict(step) for step in record.steps])
    conn = connect(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO runs "
            "(id, started_at, provider, model, prompt, answer, steps_json, "
            "plan, unverified_json, corrections) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.id,
                record.started_at,
                record.provider,
                record.model,
                record.prompt,
                record.answer,
                steps_json,
                record.plan,
                json.dumps(record.unverified),
                record.corrections,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _row_to_record(row: Any) -> RunRecord:
    raw_steps: Any = json.loads(row[6])
    raw_unverified: Any = json.loads(row[8])
    return RunRecord(
        id=row[0],
        started_at=row[1],
        provider=row[2],
        model=row[3],
        prompt=row[4],
        answer=row[5],
        steps=[Step(**step) for step in raw_steps],
        plan=row[7],
        unverified=list(raw_unverified),
        corrections=row[9],
    )


_COLUMNS = (
    "id, started_at, provider, model, prompt, answer, steps_json, "
    "plan, unverified_json, corrections"
)


def get_run(run_id: str, db_path: Path = DB_PATH) -> RunRecord | None:
    """Load one run by id or unique id-prefix; None if no match or the prefix is ambiguous."""
    if not db_path.exists():
        return None
    conn = connect(db_path)
    try:
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM runs WHERE id LIKE ? ORDER BY started_at DESC LIMIT 2",
            (run_id + "%",),
        ).fetchall()
    finally:
        conn.close()
    if len(rows) != 1:  # 0 = no match, >1 = ambiguous prefix
        return None
    return _row_to_record(rows[0])


def list_runs(
    db_path: Path = DB_PATH, limit: int = 20, only_unverified: bool = False
) -> list[RunRecord]:
    """Load the most recent run records (newest first).

    `only_unverified` narrows to the trust-review set: runs whose final answer still
    carried a figure no tool produced (self-corrected runs ended clean, so excluded).
    """
    if not db_path.exists():
        return []
    # unverified_json is '[]' when every figure was grounded (incl. after self-heal).
    where = "WHERE unverified_json != '[]' " if only_unverified else ""
    conn = connect(db_path)
    try:
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM runs {where}ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_record(row) for row in rows]
