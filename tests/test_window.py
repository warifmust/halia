"""Tests for the chat history window (token-budget cap sent to the model)."""

from typing import Any

from halia.config.settings import Config
from halia.core.agent import _window, converse
from halia.providers.base import ChatResult, Message

_CFG = Config(provider="x", model="m", base_url="u", api_key="k")


def _sys() -> Message:
    return {"role": "system", "content": "S"}


def test_small_history_unchanged() -> None:
    msgs = [_sys(), {"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
    assert _window(msgs, 10_000) is msgs  # under budget → same list, no copy


def test_trims_oldest_turns_keeps_system() -> None:
    msgs: list[Message] = [_sys()]
    for i in range(10):
        msgs.append({"role": "user", "content": f"question {i} " + "x" * 100})
        msgs.append({"role": "assistant", "content": f"answer {i} " + "y" * 100})
    windowed = _window(msgs, 500)  # only the last couple of turns fit
    assert windowed[0]["role"] == "system"  # system always retained
    assert len(windowed) < len(msgs)  # older turns dropped
    # a truncation note is inserted, then the window begins at a user boundary
    assert "trimmed" in str(windowed[1]["content"])
    assert windowed[2]["role"] == "user"
    # the most recent turn is always present
    assert windowed[-1]["content"] == msgs[-1]["content"]


def test_never_orphans_a_tool_message() -> None:
    # A tool message must be preceded by its assistant tool-call turn.
    msgs: list[Message] = [
        _sys(),
        {"role": "user", "content": "old " + "x" * 400},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]},
        {"role": "tool", "tool_call_id": "1", "content": "z" * 400},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "new question"},
        {"role": "assistant", "content": "new answer"},
    ]
    windowed = _window(msgs, 50)  # tiny budget
    # after system + truncation note, the window must start at a user message —
    # never at a bare `tool`/`assistant` turn
    assert windowed[2]["role"] == "user"
    assert not any(
        m["role"] == "tool" and windowed[i - 1].get("role") not in ("assistant",)
        for i, m in enumerate(windowed)
        if i > 0
    )


def test_last_turn_kept_even_if_over_budget() -> None:
    msgs: list[Message] = [
        _sys(),
        {"role": "user", "content": "a"},
        {"role": "user", "content": "huge " + "x" * 5000},
    ]
    windowed = _window(msgs, 10)  # smaller than the last turn
    assert windowed[0]["role"] == "system"
    assert windowed[-1]["content"].startswith("huge")  # still sent, whole


class _Recorder:
    """Captures the messages the provider actually received."""

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.seen: list[Message] = []

    def chat(self, messages: list[Message], tools: Any = None) -> ChatResult:
        self.seen = list(messages)
        return ChatResult(self.answer, [])


def test_converse_sends_windowed_history() -> None:
    from halia.skills import default_registry

    messages: list[Message] = [_sys()]
    for i in range(20):
        messages.append({"role": "user", "content": f"q{i} " + "x" * 200})
        messages.append({"role": "assistant", "content": f"a{i} " + "y" * 200})
    messages.append({"role": "user", "content": "final"})

    provider = _Recorder("ok")
    converse(messages, _CFG, default_registry(), provider=provider, history_budget=600)
    # the provider saw a trimmed view, not the whole 40-turn history
    assert len(provider.seen) < len(messages)
    assert provider.seen[0]["role"] == "system"
    assert provider.seen[-1]["content"] == "final"
    # but the caller's full transcript is untouched
    assert len(messages) == 42
