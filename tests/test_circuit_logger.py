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
        observer=None, approver=None,
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
        observer=None, approver=None,
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


def test_repetition_guard_blocks_identical_ui_action() -> None:
    """A UI tool called with the SAME args 4x runs only twice — the 3rd/4th are blocked."""
    from halia.audit.trace import Step
    from halia.core.agent import _Ctx, _execute_batch

    registry = MagicMock()
    skill = MagicMock()
    skill.name = "cua_click"
    skill.dangerous = False
    skill.run.return_value = "Clicked left"
    registry.get.return_value = skill
    registry.tool_schemas.return_value = []

    ctx = _Ctx(
        provider=MagicMock(), config=MagicMock(), registry=registry,
        prompt="t", extra_system="", plan="", max_iters=8,
        observer=None, approver=None,
        pause_on_approval=False, max_tool_failures=3,
        repeat_warn_at=2, repeat_radius=8.0,
    )
    messages: list[dict[str, Any]] = []
    steps: list[Step] = []
    call = {"name": "cua_click", "arguments": '{"x": 1, "y": 1}'}
    calls = [
        {"id": "c1", **call},
        {"id": "c2", **call},
        {"id": "c3", **call},
        {"id": "c4", **call},
    ]

    _execute_batch(ctx, calls, messages, steps)  # type: ignore[arg-type]

    assert skill.run.call_count == 2  # first two run; 3rd + 4th are blocked
    assert any("repetition guard" in m["content"] for m in messages)
    assert "repetition guard" in messages[-1]["content"]


def test_repetition_guard_disabled_explicitly() -> None:
    """repeat_warn_at=0 means identical clicks are never auto-blocked (opt-out)."""
    from halia.audit.trace import Step
    from halia.core.agent import _Ctx, _execute_batch

    registry = MagicMock()
    skill = MagicMock()
    skill.name = "cua_click"
    skill.dangerous = False
    skill.run.return_value = "Clicked left"
    registry.get.return_value = skill
    registry.tool_schemas.return_value = []

    ctx = _Ctx(
        provider=MagicMock(), config=MagicMock(), registry=registry,
        prompt="t", extra_system="", plan="", max_iters=8,
        observer=None, approver=None,
        pause_on_approval=False, max_tool_failures=3,
        repeat_warn_at=0, repeat_radius=4.0,
    )
    messages: list[dict[str, Any]] = []
    steps: list[Step] = []
    call = {"name": "cua_click", "arguments": '{"x": 1, "y": 1}'}
    calls = [{"id": f"c{i}", **call} for i in range(6)]

    _execute_batch(ctx, calls, messages, steps)  # type: ignore[arg-type]

    assert skill.run.call_count == 6  # all run — no repetition blocking
    assert not any("repetition guard" in m["content"] for m in messages)


def test_repetition_guard_allows_distinct_calls() -> None:
    """Different arguments (e.g. 6 distinct click coordinates) are NOT blocked."""
    from halia.audit.trace import Step
    from halia.core.agent import _Ctx, _execute_batch

    registry = MagicMock()
    skill = MagicMock()
    skill.name = "cua_click"
    skill.dangerous = False
    skill.run.return_value = "Clicked left"
    registry.get.return_value = skill
    registry.tool_schemas.return_value = []

    ctx = _Ctx(
        provider=MagicMock(), config=MagicMock(), registry=registry,
        prompt="t", extra_system="", plan="", max_iters=8,
        observer=None, approver=None,
        pause_on_approval=False, max_tool_failures=3,
    )
    messages: list[dict[str, Any]] = []
    steps: list[Step] = []
    coords = [(i * 100, i * 100) for i in range(6)]
    calls = [
        {"id": f"c{i}", "name": "cua_click", "arguments": json.dumps({"x": x, "y": y})}
        for i, (x, y) in enumerate(coords)
    ]

    _execute_batch(ctx, calls, messages, steps)  # type: ignore[arg-type]

    assert skill.run.call_count == 6
    assert not any("repetition guard" in m["content"] for m in messages)


def test_take_pending_image_detects_unchanged_screenshot() -> None:
    """Two identical screenshots → the second reports unchanged; a different one does not."""
    from halia.core.agent import _take_pending_image
    from halia.skills.cua import CuaScreenshot

    CuaScreenshot._last_hash = None
    CuaScreenshot._pending_image = "AAAA"
    CuaScreenshot._pending_detail = "high"

    img, detail, unchanged = _take_pending_image("cua_screenshot")
    assert (img, detail, unchanged) == ("AAAA", "high", False)

    CuaScreenshot._pending_image = "AAAA"
    CuaScreenshot._pending_detail = "high"
    img, detail, unchanged = _take_pending_image("cua_screenshot")
    assert (img, detail, unchanged) == ("AAAA", "high", True)

    CuaScreenshot._pending_image = "BBBB"
    CuaScreenshot._pending_detail = "high"
    img, detail, unchanged = _take_pending_image("cua_screenshot")
    assert (img, detail, unchanged) == ("BBBB", "high", False)

    CuaScreenshot._last_hash = None  # reset for other tests


def test_exploration_guard_blocks_pure_recon_loop() -> None:
    """A run of only screenshot/scroll recon is soft-warned, then hard-blocked."""
    from halia.audit.trace import Step
    from halia.core.agent import _Ctx, _execute_batch

    registry = MagicMock()
    skill = MagicMock()
    skill.name = "cua_screenshot"
    skill.dangerous = False
    skill.run.return_value = "Screenshot captured (1600x1039)."
    registry.get.return_value = skill
    registry.tool_schemas.return_value = []

    ctx = _Ctx(
        provider=MagicMock(), config=MagicMock(), registry=registry,
        prompt="t", extra_system="", plan="", max_iters=8,
        observer=None, approver=None,
        pause_on_approval=False, max_tool_failures=3,
        exploration_warn_at=4, exploration_block_at=8,
    )
    messages: list[dict[str, Any]] = []
    steps: list[Step] = []
    calls = [
        {"id": f"c{i}", "name": "cua_screenshot", "arguments": "{}"}
        for i in range(12)
    ]

    _execute_batch(ctx, calls, messages, steps)  # type: ignore[arg-type]

    # The first 8 run; the 9th onward are exploration-blocked.
    assert skill.run.call_count == 8
    assert any("exploration guard" in m["content"] for m in messages)
    # The soft nudge fired at the warn threshold (4).
    assert any("consecutive" in m["content"] for m in messages)


def test_exploration_counter_resets_on_progress_tool() -> None:
    """A click/read between recon steps resets the exploration budget."""
    from halia.audit.trace import Step
    from halia.core.agent import _Ctx, _execute_batch

    registry = MagicMock()
    screenshot = MagicMock()
    screenshot.name = "cua_screenshot"
    screenshot.dangerous = False
    screenshot.run.return_value = "Screenshot captured (1600x1039)."
    click = MagicMock()
    click.name = "cua_click"
    click.dangerous = False
    click.run.return_value = "Clicked left"
    registry.get.side_effect = lambda name: {
        "cua_screenshot": screenshot, "cua_click": click,
    }[name]
    registry.tool_schemas.return_value = []

    ctx = _Ctx(
        provider=MagicMock(), config=MagicMock(), registry=registry,
        prompt="t", extra_system="", plan="", max_iters=8,
        observer=None, approver=None,
        pause_on_approval=False, max_tool_failures=3,
        exploration_warn_at=2, exploration_block_at=4,
    )
    messages: list[dict[str, Any]] = []
    steps: list[Step] = []
    # screenshot, screenshot, CLICK, screenshot, screenshot, screenshot, screenshot
    calls = [
        {"id": "s1", "name": "cua_screenshot", "arguments": "{}"},
        {"id": "s2", "name": "cua_screenshot", "arguments": "{}"},
        {"id": "k1", "name": "cua_click", "arguments": '{"x": 1, "y": 1}'},
        {"id": "s3", "name": "cua_screenshot", "arguments": "{}"},
        {"id": "s4", "name": "cua_screenshot", "arguments": "{}"},
        {"id": "s5", "name": "cua_screenshot", "arguments": "{}"},
        {"id": "s6", "name": "cua_screenshot", "arguments": "{}"},
    ]

    _execute_batch(ctx, calls, messages, steps)  # type: ignore[arg-type]

    # The click reset the counter, so no exploration block fired.
    assert screenshot.run.call_count == 6
    assert click.run.call_count == 1
    assert not any("exploration guard" in m["content"] for m in messages)


def test_repetition_guard_blocks_near_identical_clicks() -> None:
    """Near-identical click coordinates (±1px nudges) trip the repetition guard."""
    from halia.audit.trace import Step
    from halia.core.agent import _Ctx, _execute_batch

    registry = MagicMock()
    skill = MagicMock()
    skill.name = "cua_click"
    skill.dangerous = False
    skill.run.return_value = "Clicked left"
    registry.get.return_value = skill
    registry.tool_schemas.return_value = []

    ctx = _Ctx(
        provider=MagicMock(), config=MagicMock(), registry=registry,
        prompt="t", extra_system="", plan="", max_iters=8,
        observer=None, approver=None,
        pause_on_approval=False, max_tool_failures=3,
        repeat_warn_at=2, repeat_radius=8.0,
    )
    messages: list[dict[str, Any]] = []
    steps: list[Step] = []
    coords = [(660, 582), (661, 583), (660, 584), (662, 583)]
    calls = [
        {"id": f"c{i}", "name": "cua_click", "arguments": json.dumps({"x": x, "y": y})}
        for i, (x, y) in enumerate(coords)
    ]

    _execute_batch(ctx, calls, messages, steps)  # type: ignore[arg-type]

    assert skill.run.call_count == 2  # first two run; 3rd + 4th are blocked
    assert any("repetition guard" in m["content"] for m in messages)


def test_screenshot_budget_blocks_run_of_too_many_screenshots() -> None:
    """Screenshots interleaved with clicks still count toward the budget."""
    from halia.audit.trace import Step
    from halia.core.agent import _Ctx, _execute_batch

    registry = MagicMock()
    screenshot = MagicMock()
    screenshot.name = "cua_screenshot"
    screenshot.dangerous = False
    screenshot.run.return_value = "Screenshot captured (1600x1039)."
    click = MagicMock()
    click.name = "cua_click"
    click.dangerous = False
    click.run.return_value = "Clicked left"
    registry.get.side_effect = lambda name: {
        "cua_screenshot": screenshot, "cua_click": click,
    }[name]
    registry.tool_schemas.return_value = []

    ctx = _Ctx(
        provider=MagicMock(), config=MagicMock(), registry=registry,
        prompt="t", extra_system="", plan="", max_iters=50,
        observer=None, approver=None,
        pause_on_approval=False, max_tool_failures=3,
        screenshot_warn_at=2, screenshot_block_at=4,
    )
    messages: list[dict[str, Any]] = []
    steps: list[Step] = []
    calls: list[dict[str, Any]] = []
    for i in range(5):
        calls.append({"id": f"s{i}", "name": "cua_screenshot", "arguments": "{}"})
        # Far-apart click targets so the proximity guard doesn't interfere.
        calls.append(
            {"id": f"c{i}", "name": "cua_click",
             "arguments": json.dumps({"x": i * 100, "y": i * 100})}
        )

    _execute_batch(ctx, calls, messages, steps)  # type: ignore[arg-type]

    # Screenshots run up to the block threshold (4); the 5th is budget-blocked.
    assert screenshot.run.call_count == 4
    assert any("screenshot budget exceeded" in m["content"] for m in messages)
    # Clicks still ran — they don't reset the screenshot counter.
    assert click.run.call_count == 5


def test_screenshot_block_disabled_when_block_at_zero() -> None:
    """block_at=0 (the new default) disables the hard cap: every screenshot runs."""
    from halia.audit.trace import Step
    from halia.core.agent import _Ctx, _execute_batch

    registry = MagicMock()
    screenshot = MagicMock()
    screenshot.name = "cua_screenshot"
    screenshot.dangerous = False
    screenshot.run.return_value = "Screenshot captured (1600x1039)."
    registry.get.return_value = screenshot
    registry.tool_schemas.return_value = []

    ctx = _Ctx(
        provider=MagicMock(), config=MagicMock(), registry=registry,
        prompt="t", extra_system="", plan="", max_iters=8,
        observer=None, approver=None,
        pause_on_approval=False, max_tool_failures=3,
        screenshot_warn_at=0, screenshot_block_at=0,
        exploration_block_at=1000,  # isolate from the exploration guard
    )
    messages: list[dict[str, Any]] = []
    steps: list[Step] = []
    calls = [
        {"id": f"s{i}", "name": "cua_screenshot", "arguments": "{}"}
        for i in range(50)
    ]

    _execute_batch(ctx, calls, messages, steps)  # type: ignore[arg-type]

    assert screenshot.run.call_count == 50
    assert not any("screenshot budget exceeded" in m["content"] for m in messages)


def test_wrap_up_note_injected_near_turn_cap() -> None:
    """The turn-budget wrap-up note fires only when few turns remain."""
    from halia.core.agent import _compose_turn_note, _Ctx

    ctx = _Ctx(
        provider=MagicMock(), config=MagicMock(), registry=MagicMock(),
        prompt="t", extra_system="", plan="", max_iters=8,
        observer=None, approver=None,
        pause_on_approval=False,
    )
    # Far from the cap: no wrap-up note.
    assert _compose_turn_note(ctx, 2) == ""
    # Near the cap (3 turns left): wrap-up note present.
    note = _compose_turn_note(ctx, 6)
    assert "FINISH" in note
    assert "6/8" in note


def test_stuck_increments_on_blocked_repeat_and_resets_on_progress() -> None:
    """Blocked UI repeats count toward STUCK; a successful different action resets it."""
    from halia.audit.trace import Step
    from halia.core.agent import _Ctx, _execute_batch

    registry = MagicMock()
    skill = MagicMock()
    skill.name = "cua_click"
    skill.dangerous = False
    skill.run.return_value = "Clicked left"
    registry.get.return_value = skill
    registry.tool_schemas.return_value = []

    ctx = _Ctx(
        provider=MagicMock(), config=MagicMock(), registry=registry,
        prompt="t", extra_system="", plan="", max_iters=8,
        observer=None, approver=None,
        pause_on_approval=False, max_tool_failures=3,
        repeat_warn_at=2, repeat_radius=8.0,
    )
    messages: list[dict[str, Any]] = []
    steps: list[Step] = []

    def _click(cid: str, x: int, y: int) -> dict[str, Any]:
        return {"id": cid, "name": "cua_click", "arguments": json.dumps({"x": x, "y": y})}

    # Three identical clicks: 1st + 2nd run, 3rd is blocked → one no-progress signal.
    _execute_batch(
        ctx,
        [_click("a1", 10, 10), _click("a2", 10, 10), _click("a3", 10, 10)],
        messages, steps,
    )  # type: ignore[arg-type]
    assert ctx._stuck == 1

    # A genuinely different click (far away) runs and resets the counter.
    _execute_batch(ctx, [_click("b1", 500, 500)], messages, steps)  # type: ignore[arg-type]
    assert ctx._stuck == 0


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
            prompt="t", extra_system="", plan="", max_iters=8,
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
        observer=None, approver=None,
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
