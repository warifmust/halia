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
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from halia.audit.trace import Step
from halia.store.database import DB_PATH, connect


@dataclass(frozen=True)
class RunRecord:
    """The persisted record of one run."""

    id: str
    started_at: str  # ISO 8601, UTC
    provider: str
    model: str
    prompt: str
    answer: str
    steps: list[Step]


def new_record(
    provider: str, model: str, prompt: str, answer: str, steps: list[Step]
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
    )


def save_run(record: RunRecord, db_path: Path = DB_PATH) -> None:
    """Persist a run record to the database."""
    steps_json = json.dumps([asdict(step) for step in record.steps])
    conn = connect(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO runs "
            "(id, started_at, provider, model, prompt, answer, steps_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                record.id,
                record.started_at,
                record.provider,
                record.model,
                record.prompt,
                record.answer,
                steps_json,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def list_runs(db_path: Path = DB_PATH, limit: int = 20) -> list[RunRecord]:
    """Load the most recent run records (newest first)."""
    if not db_path.exists():
        return []
    conn = connect(db_path)
    try:
        cursor = conn.execute(
            "SELECT id, started_at, provider, model, prompt, answer, steps_json "
            "FROM runs ORDER BY started_at DESC LIMIT ?",
            (limit,),
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    records: list[RunRecord] = []
    for row in rows:
        raw_steps: Any = json.loads(row[6])
        steps = [Step(**step) for step in raw_steps]
        records.append(
            RunRecord(
                id=row[0],
                started_at=row[1],
                provider=row[2],
                model=row[3],
                prompt=row[4],
                answer=row[5],
                steps=steps,
            )
        )
    return records
