"""halia.eval — deterministic computer-use eval harness (no LLM-as-a-judge)."""

from halia.eval.harness import (
    AutoApprove,
    Task,
    TaskOutcome,
    Verdict,
    resolve_config,
    run_task,
)
from halia.eval.tasks import ALL_TASKS, CUA_DRAWING, make_api_trigger_task

__all__ = [
    "ALL_TASKS",
    "CUA_DRAWING",
    "make_api_trigger_task",
    "AutoApprove",
    "Task",
    "TaskOutcome",
    "Verdict",
    "resolve_config",
    "run_task",
]
