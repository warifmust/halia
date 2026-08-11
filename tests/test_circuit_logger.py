"""Tests for circuit breaker and structured logging."""

from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import MagicMock

# --- Circuit breaker ---


def test_circuit_breaker_skips_after_consecutive_failures() -> None:
    """A tool that fails 3 times consecutively is skipped by the circuit breaker."""
    from halia.audit.trace import Step
    from halia.core.agent import _Ctx, _execute_batch

    registry = MagicMock()
    skill = MagicMock()
    skill.name = "flaky_tool"
    skill.dangerous = False
    skill.run.return_value = "error: connection refused"
    registry.get.return_value = skill
    registry.tool_schemas.return_value = []

    ctx = _Ctx(
        provider=MagicMock(), config=MagicMock(), registry=registry,
        prompt="test", extra_system="", plan="", max_iters=8,
        max_corrections=1, observer=None, approver=None,
        pause_on_approval=False, max_tool_failures=3,
    )
    messages: list[dict[str, Any]] = []
    steps: list[Step] = []
    calls = [
        {"id": "c1", "name": "flaky_tool", "arguments": "{}"},
        {"id": "c2", "name": "flaky_tool", "arguments": "{}"},
        {"id": "c3", "name": "flaky_tool", "arguments": "{}"},
        {"id": "c4", "name": "flaky_tool", "arguments": "{}"},
    ]

    _execute_batch(ctx, calls, messages, steps)  # type: ignore[arg-type]

    # First 3 calls run the tool; 4th is circuit-broken.
    assert skill.run.call_count == 3
    assert len(messages) == 4
    assert "circuit breaker" in messages[3]["content"]


def test_circuit_breaker_resets_on_success() -> None:
    """A successful call resets the failure counter."""
    from halia.audit.trace import Step
    from halia.core.agent import _Ctx, _execute_batch

    registry = MagicMock()
    skill = MagicMock()
    skill.name = "flaky_tool"
    skill.dangerous = False
    # First call fails, second succeeds, third fails — counter resets.
    skill.run.side_effect = ["error: timeout", "success", "error: timeout"]
    registry.get.return_value = skill
    registry.tool_schemas.return_value = []

    ctx = _Ctx(
        provider=MagicMock(), config=MagicMock(), registry=registry,
        prompt="test", extra_system="", plan="", max_iters=8,
        max_corrections=1, observer=None, approver=None,
        pause_on_approval=False, max_tool_failures=3,
    )
    messages: list[dict[str, Any]] = []
    steps: list[Step] = []
    calls = [
        {"id": "c1", "name": "flaky_tool", "arguments": "{}"},
        {"id": "c2", "name": "flaky_tool", "arguments": "{}"},  # success resets
        {"id": "c3", "name": "flaky_tool", "arguments": "{}"},  # starts counting again
    ]

    _execute_batch(ctx, calls, messages, steps)  # type: ignore[arg-type]

    assert skill.run.call_count == 3  # all 3 ran (counter reset after success)
    assert ctx._tool_failures.get("flaky_tool", 0) == 1


# --- Structured logging ---


def test_execute_batch_honors_check_read_for_read_tools() -> None:
    """A read tool is gated by the approver's `check_read` (the gate the persona TUI dropped)."""
    from halia.audit.trace import Step
    from halia.core.agent import _Ctx, _execute_batch

    def _registry() -> Any:
        registry = MagicMock()
        skill = MagicMock()
        skill.name = "read_file"
        skill.dangerous = False
        skill.run.return_value = "file contents"
        registry.get.return_value = skill
        registry.tool_schemas.return_value = []
        return registry, skill

    class Approver:
        def __init__(self, allow: bool) -> None:
            self.allow = allow

        def __call__(self, name: str, arguments: str) -> bool:
            return True

        def check_read(self, name: str, arguments: str) -> bool:
            return self.allow

    call = [{"id": "c1", "name": "read_file", "arguments": '{"path": "/x/a.txt"}'}]

    def _run(approver: Any) -> tuple[Any, list[dict[str, Any]]]:
        registry, skill = _registry()
        ctx = _Ctx(
            provider=MagicMock(), config=MagicMock(), registry=registry,
            prompt="t", extra_system="", plan="", max_iters=8, max_corrections=1,
            observer=None, approver=approver, pause_on_approval=False, max_tool_failures=3,
        )
        messages: list[dict[str, Any]] = []
        steps: list[Step] = []
        _execute_batch(ctx, call, messages, steps)  # type: ignore[arg-type]
        return skill, messages

    # check_read denies → tool never runs, denial observation recorded
    skill, messages = _run(Approver(False))
    assert skill.run.call_count == 0
    assert "not approved" in messages[0]["content"]

    # check_read allows → tool runs
    skill, _ = _run(Approver(True))
    assert skill.run.call_count == 1

    # no check_read attribute (the old TUI wrapper) → read runs ungated (documents the bug)
    skill, _ = _run(lambda name, args: True)
    assert skill.run.call_count == 1


def test_log_event_writes_jsonl(tmp_path: Any) -> None:
    """log_event writes a JSON line to the configured file."""
    from halia.audit import logger

    log_file = tmp_path / "test.jsonl"
    # Reset the module's initialized state.
    logger._initialized = False
    logger._log_file = None
    os.environ["HALIA_LOG"] = str(log_file)
    os.environ.pop("HALIA_LOG_LEVEL", None)

    logger.log_event("test_event", tool="calc", duration_ms=42)

    logger._initialized = False  # reset for other tests
    os.environ.pop("HALIA_LOG", None)

    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["event"] == "test_event"
    assert entry["tool"] == "calc"
    assert entry["duration_ms"] == 42
    assert "ts" in entry


def test_execute_batch_emits_tool_call_events(tmp_path: Any) -> None:
    """_execute_batch logs a tool_call JSONL event per call: ok, error, then skipped."""
    from halia.audit import logger
    from halia.audit.trace import Step
    from halia.core.agent import _Ctx, _execute_batch

    log_file = tmp_path / "tools.jsonl"
    logger._initialized = False
    logger._log_file = None
    os.environ["HALIA_LOG"] = str(log_file)
    os.environ.pop("HALIA_LOG_LEVEL", None)

    registry = MagicMock()
    skill = MagicMock()
    skill.name = "svc"
    skill.dangerous = False
    # ok resets; then two failures reach max_tool_failures=2, so the 4th call is skipped.
    # (The failure counter increments AFTER a run, so a skip needs max+1 calls.)
    skill.run.side_effect = ["ok result", "error: boom", "error: boom"]
    registry.get.return_value = skill
    registry.tool_schemas.return_value = []

    ctx = _Ctx(
        provider=MagicMock(), config=MagicMock(), registry=registry,
        prompt="t", extra_system="", plan="", max_iters=8,
        max_corrections=1, observer=None, approver=None,
        pause_on_approval=False, max_tool_failures=2,
    )
    messages: list[dict[str, Any]] = []
    steps: list[Step] = []
    calls = [
        {"id": "c1", "name": "svc", "arguments": "{}"},
        {"id": "c2", "name": "svc", "arguments": "{}"},
        {"id": "c3", "name": "svc", "arguments": "{}"},
        {"id": "c4", "name": "svc", "arguments": "{}"},  # circuit-broken → skipped
    ]

    _execute_batch(ctx, calls, messages, steps)  # type: ignore[arg-type]

    logger._initialized = False
    os.environ.pop("HALIA_LOG", None)

    events = [json.loads(ln) for ln in log_file.read_text().strip().split("\n")]
    tool_calls = [e for e in events if e["event"] == "tool_call"]
    assert [e["status"] for e in tool_calls] == ["ok", "error", "error", "skipped"]
    assert skill.run.call_count == 3  # 4th never ran
    assert all(e["tool"] == "svc" for e in tool_calls)


def test_log_event_respects_level(tmp_path: Any) -> None:
    """Events below the configured level are not written."""
    from halia.audit import logger

    log_file = tmp_path / "test.jsonl"
    logger._initialized = False
    logger._log_file = None
    os.environ["HALIA_LOG"] = str(log_file)
    os.environ["HALIA_LOG_LEVEL"] = "warn"

    logger.log_event("debug_event", level="debug")
    logger.log_event("info_event", level="info")
    logger.log_event("warn_event", level="warn")

    logger._initialized = False
    os.environ.pop("HALIA_LOG", None)
    os.environ.pop("HALIA_LOG_LEVEL", None)

    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 1
    assert json.loads(lines[0])["event"] == "warn_event"
