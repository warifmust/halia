"""halia.eval — deterministic computer-use eval harness (no LLM-as-a-judge)."""

from halia.eval.harness import (
    AutoApprove,
    Task,
    TaskOutcome,
    Verdict,
    resolve_config,
    run_task,
)
from halia.eval.tasks import ALL_TASKS, SAUCEDEMO_ADD_6, SWAGGER_CMN_TRIGGER

__all__ = [
    "ALL_TASKS",
    "SAUCEDEMO_ADD_6",
    "SWAGGER_CMN_TRIGGER",
    "AutoApprove",
    "Task",
    "TaskOutcome",
    "Verdict",
    "resolve_config",
    "run_task",
]
