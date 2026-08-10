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
    assert "secret sauce" in result.steps[0].observation
    assert "UNTRUSTED SOURCE" in result.steps[0].observation


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


def _balanced(messages: list[Message]) -> bool:
    """Every assistant tool_calls message is followed by a tool response for each call."""
    i = 0
    while i < len(messages):
        m = messages[i]
        if m.get("role") == "assistant" and m.get("tool_calls"):
            n = len(m["tool_calls"])
            j, count = i + 1, 0
            while j < len(messages) and messages[j].get("role") == "tool":
                count += 1
                j += 1
            if count < n:
                return False
            i = j
        else:
            i += 1
    return True


def test_runlimit_leaves_balanced_messages() -> None:
    # A model that never stops calling tools → converse hits the cap with tool exchanges
    # already appended to the caller's messages. Those must stay BALANCED (no dangling
    # tool_calls without responses) so the next request can't 400. Regression for the TUI
    # bug where a blind messages.pop() orphaned the final tool_calls after the cap.
    from halia.core.agent import converse

    looping = ChatResult(
        content=None, tool_calls=[ToolCall(id="1", name="list_files", arguments="{}")]
    )
    provider = FakeProvider([looping] * 10)
    messages: list[Message] = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "go"},
    ]
    with pytest.raises(RunLimitError):
        converse(messages, _CFG, default_registry(), provider=provider, max_iters=3)
    assert _balanced(messages)
    assert messages[-1]["role"] == "tool"  # stopped on a complete batch


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


def test_conscience_corrects_ungrounded_figure(tmp_path: Any) -> None:
    # A tool observation grounds the *correct* number; the model first invents a wrong one.
    note = tmp_path / "note.txt"
    note.write_text("audited total 550.50")
    provider = FakeProvider(
        [
            # 1) invented figure, no tool used → conscience bounces it back
            ChatResult(content="Bank total is 730.50.", tool_calls=[]),
            # 2) after the bounce, the model grounds it via a tool
            ChatResult(
                content=None,
                tool_calls=[ToolCall(id="1", name="read_file", arguments=f'{{"path": "{note}"}}')],
            ),
            # 3) corrected answer, now traceable to the observation
            ChatResult(content="Corrected: the total is 550.50.", tool_calls=[]),
        ]
    )
    result = run("total it", _CFG, default_registry(), provider=provider)
    assert "550.50" in result.answer
    assert result.unverified == []  # regrounded
    assert result.corrections == 1
    assert provider.calls == 3


def test_conscience_bounces_only_up_to_the_budget() -> None:
    # The model refuses to ground it; with a 1-correction budget it's returned still-flagged.
    provider = FakeProvider(
        [
            ChatResult(content="Total is 730.50.", tool_calls=[]),
            ChatResult(content="Still 730.50, trust me.", tool_calls=[]),
        ]
    )
    result = run("total it", _CFG, default_registry(), provider=provider, max_corrections=1)
    assert result.unverified == ["730.50"]
    assert result.corrections == 1
    assert provider.calls == 2


def test_no_correction_when_grounded(tmp_path: Any) -> None:
    note = tmp_path / "n.txt"
    note.write_text("balance 42.00")
    provider = FakeProvider(
        [
            ChatResult(
                content=None,
                tool_calls=[ToolCall(id="1", name="read_file", arguments=f'{{"path": "{note}"}}')],
            ),
            ChatResult(content="The balance is 42.00.", tool_calls=[]),
        ]
    )
    result = run("x", _CFG, default_registry(), provider=provider)
    assert result.unverified == []
    assert result.corrections == 0
    assert provider.calls == 2  # no extra bounce


def test_run_does_not_double_apply_persona_overlay(monkeypatch: Any) -> None:
    # Regression: the CLI injects PERSONA.md into extra_system; run() must NOT re-add it
    # (doing both duplicated the overlay in the system prompt).
    import halia.core.agent as agent_mod

    monkeypatch.setattr(agent_mod, "persona_overlay", lambda: "[PERSONA_MARKER]")
    captured: dict[str, str] = {}

    class Recorder:
        def chat(self, messages: list[Message], tools: Any = None) -> ChatResult:
            captured["system"] = str(messages[0]["content"])
            return ChatResult(content="done", tool_calls=[])

    run("hi", _CFG, default_registry(), provider=Recorder(), extra_system="[PERSONA_MARKER]")
    assert captured["system"].count("[PERSONA_MARKER]") == 1  # once (from extra_system), not twice
