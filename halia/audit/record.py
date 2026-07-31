"""Durable audit trail — persist each run's provenance to disk.

A trust product keeps the receipts. Every `halia run` is written to
`~/.halia/runs/<ts>_<id>.json` so it can be reviewed, reproduced, and (later)
verified against — the audit layer maturing from "shown once" to "recorded".

`~/.halia` is inside the permission floor, so the agent's own read_file can't
read the audit logs — the harness records them; the model can't rummage through them.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from halia.audit.trace import Step

RUNS_DIR = Path.home() / ".halia" / "runs"


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


def save_run(record: RunRecord, runs_dir: Path = RUNS_DIR) -> Path:
    """Write a run record to disk; returns the file path."""
    runs_dir.mkdir(parents=True, exist_ok=True)
    stamp = record.started_at.replace(":", "-")
    path = runs_dir / f"{stamp}_{record.id}.json"
    path.write_text(json.dumps(asdict(record), indent=2))
    return path


def list_runs(runs_dir: Path = RUNS_DIR, limit: int = 20) -> list[RunRecord]:
    """Load the most recent run records (newest first)."""
    if not runs_dir.is_dir():
        return []
    records: list[RunRecord] = []
    for file in sorted(runs_dir.glob("*.json"), reverse=True)[:limit]:
        data: Any = json.loads(file.read_text())
        steps = [Step(**s) for s in data.get("steps", [])]
        records.append(
            RunRecord(
                id=data["id"],
                started_at=data["started_at"],
                provider=data["provider"],
                model=data["model"],
                prompt=data["prompt"],
                answer=data["answer"],
                steps=steps,
            )
        )
    return records
