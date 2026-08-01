"""Tests for the chat/converse primitive (multi-turn conversation)."""

from typing import Any

from halia.config.settings import Config
from halia.core.agent import converse
from halia.providers.base import ChatResult, Message, ToolCall
from halia.skills import default_registry

_CFG = Config(provider="x", model="m", base_url="u", api_key="k")


class FakeProvider:
    def __init__(self, results: list[ChatResult]) -> None:
        self._results = results
        self.calls = 0
        self.seen: list[list[Message]] = []

    def chat(self, messages: list[Message], tools: Any = None) -> ChatResult:
        self.seen.append(list(messages))
        result = self._results[self.calls]
        self.calls += 1
        return result


def test_converse_keeps_context_across_turns() -> None:
    provider = FakeProvider(
        [ChatResult("hi there", []), ChatResult("as I said, 42", [])]
    )
    messages: list[Message] = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
    ]
    r1 = converse(messages, _CFG, default_registry(), provider=provider)
    assert r1.answer == "hi there"
    messages.append({"role": "assistant", "content": r1.answer})

    messages.append({"role": "user", "content": "what did you say?"})
    r2 = converse(messages, _CFG, default_registry(), provider=provider)
    assert r2.answer == "as I said, 42"
    # the second turn saw the full prior conversation (system + 3 turns)
    assert len(provider.seen[1]) == 4
    assert provider.seen[1][1]["content"] == "hello"


def test_converse_runs_tools_within_a_turn(tmp_path: Any) -> None:
    note = tmp_path / "n.txt"
    note.write_text("the answer is 7")
    provider = FakeProvider(
        [
            ChatResult(
                content=None,
                tool_calls=[ToolCall(id="1", name="read_file", arguments=f'{{"path": "{note}"}}')],
            ),
            ChatResult(content="it says 7", tool_calls=[]),
        ]
    )
    messages: list[Message] = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "read the note"},
    ]
    result = converse(messages, _CFG, default_registry(), provider=provider)
    assert result.answer == "it says 7"
    assert result.steps[0].tool == "read_file"
    # the tool exchange was appended to the caller's messages (context for next turn)
    assert any(m.get("role") == "tool" for m in messages)
