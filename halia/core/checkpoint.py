"""Checkpoints — a run's loop state, frozen so it can be resumed later.

This is halia's HITL / pause-resume primitive. When a run needs a human decision
it can't get right now (a dangerous tool with no one at the keyboard), the loop
freezes its entire state — the conversation so far, the steps, the pending tool
batch awaiting approval — into a checkpoint, and stops. `halia resume` rehydrates
it, applies the approve/deny decision, and continues from exactly where it paused.

The messages are plain JSON dicts and the steps/pending are simple records, so the
whole loop state serializes cleanly. Secrets are NOT stored: the api key is re-read
from the user's config at resume, never persisted in a checkpoint.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from halia.audit.trace import Step
from halia.providers.base import Message, ToolCall
from halia.store.database import DB_PATH, connect


@dataclass(frozen=True)
class Checkpoint:
    """The frozen state of a paused run — everything needed to resume it."""

    id: str
    created_at: str  # ISO 8601, UTC
    prompt: str
    provider: str
    model: str
    skills: list[str]  # to rebuild the registry at resume
    extra_system: str
    plan: str
    messages: list[Message]  # the conversation so far (incl. the pending assistant turn)
    steps: list[Step]
    pending: list[ToolCall]  # the tool-call batch awaiting a decision
    iters_used: int  # iterations already spent (budget spans the pause)
    corrections: int
    reason: str  # why it paused, e.g. "approval required: write_file"


def new_checkpoint(
    prompt: str,
    provider: str,
    model: str,
    skills: list[str],
    extra_system: str,
    plan: str,
    messages: list[Message],
    steps: list[Step],
    pending: list[ToolCall],
    iters_used: int,
    corrections: int,
    reason: str,
) -> Checkpoint:
    """Build a Checkpoint with a fresh id + timestamp."""
    return Checkpoint(
        id=uuid.uuid4().hex[:12],
        created_at=datetime.now(UTC).isoformat(),
        prompt=prompt,
        provider=provider,
        model=model,
        skills=list(skills),
        extra_system=extra_system,
        plan=plan,
        messages=list(messages),
        steps=list(steps),
        pending=list(pending),
        iters_used=iters_used,
        corrections=corrections,
        reason=reason,
    )


_COLUMNS = (
    "id, created_at, prompt, provider, model, skills_json, extra_system, plan, "
    "messages_json, steps_json, pending_json, iters_used, corrections, reason"
)


def save_checkpoint(cp: Checkpoint, db_path: Path = DB_PATH) -> None:
    """Persist a checkpoint."""
    conn = connect(db_path)
    try:
        conn.execute(
            f"INSERT OR REPLACE INTO checkpoints ({_COLUMNS}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cp.id,
                cp.created_at,
                cp.prompt,
                cp.provider,
                cp.model,
                json.dumps(cp.skills),
                cp.extra_system,
                cp.plan,
                json.dumps(cp.messages),
                json.dumps([_step_dict(s) for s in cp.steps]),
                json.dumps(cp.pending),
                cp.iters_used,
                cp.corrections,
                cp.reason,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _step_dict(step: Step) -> dict[str, str]:
    return {"tool": step.tool, "arguments": step.arguments, "observation": step.observation}


def _row_to_checkpoint(row: Any) -> Checkpoint:
    raw_steps: Any = json.loads(row[9])
    raw_pending: Any = json.loads(row[10])
    return Checkpoint(
        id=row[0],
        created_at=row[1],
        prompt=row[2],
        provider=row[3],
        model=row[4],
        skills=list(json.loads(row[5])),
        extra_system=row[6],
        plan=row[7],
        messages=list(json.loads(row[8])),
        steps=[Step(**s) for s in raw_steps],
        pending=list(raw_pending),
        iters_used=row[11],
        corrections=row[12],
        reason=row[13],
    )


def get_checkpoint(checkpoint_id: str, db_path: Path = DB_PATH) -> Checkpoint | None:
    """Load one checkpoint by id or unique prefix; None if missing or ambiguous."""
    if not db_path.exists():
        return None
    conn = connect(db_path)
    try:
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM checkpoints WHERE id LIKE ? "
            "ORDER BY created_at DESC LIMIT 2",
            (checkpoint_id + "%",),
        ).fetchall()
    finally:
        conn.close()
    if len(rows) != 1:  # 0 = none, >1 = ambiguous
        return None
    return _row_to_checkpoint(rows[0])


def list_checkpoints(db_path: Path = DB_PATH, limit: int = 20) -> list[Checkpoint]:
    """Load the most recent checkpoints (newest first) — the pending-approval queue."""
    if not db_path.exists():
        return []
    conn = connect(db_path)
    try:
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM checkpoints ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_checkpoint(row) for row in rows]


def delete_checkpoint(checkpoint_id: str, db_path: Path = DB_PATH) -> bool:
    """Delete a checkpoint (called once it's resumed to completion). True if it existed."""
    conn = connect(db_path)
    try:
        cursor = conn.execute("DELETE FROM checkpoints WHERE id = ?", (checkpoint_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
