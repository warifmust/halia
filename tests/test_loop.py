"""Tests for the agent tool-calling loop (fake provider — no network)."""

from typing import Any

import pytest

from halia.audit.trace import Step
from halia.config.settings import Config
from halia.core.agent import RunLimitError, run
from halia.providers.base import ChatResult, Message, ToolCall
from halia.skills import default_registry

_CFG = Config(provider="x", model="m", base_url="u", api_key="k")


class FakeProvider:
    """Returns a pre-scripted sequence of ChatResults."""

    def __init__(self, results: list[ChatResult]) -> None:
        self._results = results
        self.calls = 0

    def chat(self, messages: list[Message], tools: Any = None) -> ChatResult:
        result = self._results[self.calls]
        self.calls += 1
        return result


def test_loop_executes_tool_then_answers(tmp_path: Any) -> None:
    target = tmp_path / "hello.txt"
    target.write_text("secret sauce")
    provider = FakeProvider(
        [
            ChatResult(
                content=None,
                tool_calls=[
                    ToolCall(id="1", name="read_file", arguments=f'{{"path": "{target}"}}')
                ],
            ),
            ChatResult(content="the file says: secret sauce", tool_calls=[]),
        ]
    )
    result = run("read the file", _CFG, default_registry(), provider=provider)
    assert result.answer == "the file says: secret sauce"
    assert provider.calls == 2
    # provenance recorded
    assert len(result.steps) == 1
    assert result.steps[0].tool == "read_file"
    assert result.steps[0].observation == "secret sauce"


def test_observer_sees_each_step(tmp_path: Any) -> None:
    provider = FakeProvider(
        [
            ChatResult(
                content=None,
                tool_calls=[ToolCall(id="1", name="list_files", arguments="{}")],
            ),
            ChatResult(content="done", tool_calls=[]),
        ]
    )
    seen: list[Step] = []
    run("x", _CFG, default_registry(), provider=provider, observer=seen.append)
    assert len(seen) == 1
    assert seen[0].tool == "list_files"


def test_loop_hits_iteration_cap() -> None:
    looping = ChatResult(
        content=None, tool_calls=[ToolCall(id="1", name="list_files", arguments="{}")]
    )
    provider = FakeProvider([looping] * 10)
    with pytest.raises(RunLimitError, match="iteration cap"):
        run("loop", _CFG, default_registry(), provider=provider, max_iters=3)
    assert provider.calls == 3


def test_unknown_tool_becomes_observation() -> None:
    provider = FakeProvider(
        [
            ChatResult(content=None, tool_calls=[ToolCall(id="1", name="nope", arguments="{}")]),
            ChatResult(content="handled it", tool_calls=[]),
        ]
    )
    result = run("x", _CFG, default_registry(), provider=provider)
    assert result.answer == "handled it"
    assert "unknown tool" in result.steps[0].observation


def _command_run() -> "FakeProvider":
    return FakeProvider(
        [
            ChatResult(
                content=None,
                tool_calls=[
                    ToolCall(id="1", name="run_command", arguments='{"command": "echo hi"}')
                ],
            ),
            ChatResult(content="ok", tool_calls=[]),
        ]
    )


def test_dangerous_tool_blocked_without_approver() -> None:
    registry = default_registry(allow_commands=True)
    result = run("x", _CFG, registry, provider=_command_run())  # approver=None
    assert "blocked" in result.steps[0].observation


def test_default_write_file_needs_approval(tmp_path: Any) -> None:
    target = tmp_path / "out.txt"
    provider = FakeProvider(
        [
            ChatResult(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="1",
                        name="write_file",
                        arguments=f'{{"path": "{target}", "content": "hi"}}',
                    )
                ],
            ),
            ChatResult(content="done", tool_calls=[]),
        ]
    )
    # write_file is dangerous and in the DEFAULT registry → blocked without an approver
    result = run("x", _CFG, default_registry(), provider=provider)
    assert "blocked" in result.steps[0].observation
    assert not target.exists()
    # with an approving approver → it writes
    provider2 = FakeProvider(
        [
            ChatResult(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="1",
                        name="write_file",
                        arguments=f'{{"path": "{target}", "content": "hi"}}',
                    )
                ],
            ),
            ChatResult(content="done", tool_calls=[]),
        ]
    )
    run("x", _CFG, default_registry(), provider=provider2, approver=lambda n, a: True)
    assert target.read_text() == "hi"


def test_dangerous_tool_denied_by_approver() -> None:
    registry = default_registry(allow_commands=True)
    result = run("x", _CFG, registry, provider=_command_run(), approver=lambda n, a: False)
    assert "denied by user" in result.steps[0].observation


def test_dangerous_tool_allowed_by_approver() -> None:
    registry = default_registry(allow_commands=True)
    result = run("x", _CFG, registry, provider=_command_run(), approver=lambda n, a: True)
    assert "exit_code: 0" in result.steps[0].observation
    assert "hi" in result.steps[0].observation
