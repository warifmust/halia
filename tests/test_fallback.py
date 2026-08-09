"""Tests for FallbackProvider — retry on failure, but never double-emit mid-stream."""

from __future__ import annotations

from typing import Any

import pytest

from halia.providers.base import ChatResult, DeltaObserver, Message, ProviderError, Usage
from halia.providers.fallback import FallbackProvider


class _Ok:
    """A provider that succeeds, optionally streaming a couple of tokens first."""

    def __init__(self, text: str = "ok", stream: bool = False) -> None:
        self.text = text
        self.stream = stream
        self.calls = 0

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        on_delta: DeltaObserver | None = None,
    ) -> ChatResult:
        self.calls += 1
        if self.stream and on_delta is not None:
            on_delta(self.text)
        return ChatResult(content=self.text, tool_calls=[], usage=Usage())


class _Fail:
    """A provider that raises ProviderError, optionally after emitting a delta first."""

    def __init__(self, emit_first: bool = False) -> None:
        self.emit_first = emit_first
        self.calls = 0

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        on_delta: DeltaObserver | None = None,
    ) -> ChatResult:
        self.calls += 1
        if self.emit_first and on_delta is not None:
            on_delta("partial ")
        raise ProviderError("boom")


def _msgs() -> list[Message]:
    return [{"role": "user", "content": "hi"}]


def test_falls_back_when_primary_fails_before_emitting() -> None:
    primary, backup = _Fail(), _Ok(text="from-backup")
    result = FallbackProvider([primary, backup]).chat(_msgs())
    assert result.content == "from-backup"
    assert primary.calls == 1 and backup.calls == 1


def test_no_fallback_after_mid_stream_emission() -> None:
    # Primary streams a token, THEN fails → we must NOT fall back (would duplicate output).
    seen: list[str] = []
    primary, backup = _Fail(emit_first=True), _Ok(text="from-backup")
    with pytest.raises(ProviderError):
        FallbackProvider([primary, backup]).chat(_msgs(), on_delta=seen.append)
    assert seen == ["partial "]      # user saw the partial token once
    assert backup.calls == 0         # backup was never tried


def test_first_success_short_circuits() -> None:
    primary, backup = _Ok(text="first"), _Ok(text="second")
    result = FallbackProvider([primary, backup]).chat(_msgs())
    assert result.content == "first"
    assert backup.calls == 0


def test_all_fail_raises_last_error() -> None:
    a, b = _Fail(), _Fail()
    with pytest.raises(ProviderError):
        FallbackProvider([a, b]).chat(_msgs())
    assert a.calls == 1 and b.calls == 1


def test_empty_providers_rejected() -> None:
    with pytest.raises(ValueError, match="at least one"):
        FallbackProvider([])
