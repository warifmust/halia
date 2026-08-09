"""Tests for stdin piping, --json output, and provider fallback."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from halia.providers.base import ChatResult, Provider, ProviderError, Usage
from halia.providers.fallback import FallbackProvider  # noqa: I001

# --- Stdin piping ---


def test_read_stdin_returns_empty_for_tty(monkeypatch: Any) -> None:
    """_read_stdin returns '' when stdin is a TTY."""
    import sys

    from halia.cli.main import _read_stdin

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    assert _read_stdin() == ""


def test_read_stdin_captures_piped_data(monkeypatch: Any) -> None:
    """_read_stdin returns the piped content."""
    import sys

    from halia.cli.main import _read_stdin

    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(sys.stdin, "read", lambda: "hello from pipe\n")
    assert _read_stdin() == "hello from pipe"


# --- FallbackProvider ---


def _ok_provider(content: str = "ok") -> MagicMock:
    p = MagicMock(spec=Provider)
    p.chat.return_value = ChatResult(content=content, tool_calls=[], usage=Usage(prompt_tokens=10))
    return p


def _failing_provider(msg: str = "down") -> MagicMock:
    p = MagicMock(spec=Provider)
    p.chat.side_effect = ProviderError(msg)
    return p


def test_fallback_uses_first_when_it_works() -> None:
    ok = _ok_provider("first")
    fallback = FallbackProvider([ok])
    result = fallback.chat([{"role": "user", "content": "hi"}])
    assert result.content == "first"
    ok.chat.assert_called_once()


def test_fallback_skips_failed_provider() -> None:
    bad = _failing_provider()
    good = _ok_provider("second")
    fallback = FallbackProvider([bad, good])
    result = fallback.chat([{"role": "user", "content": "hi"}])
    assert result.content == "second"


def test_fallback_raises_when_all_fail() -> None:
    a = _failing_provider("a down")
    b = _failing_provider("b down")
    fallback = FallbackProvider([a, b])
    with pytest.raises(ProviderError, match="b down"):
        fallback.chat([{"role": "user", "content": "hi"}])


def test_fallback_requires_at_least_one() -> None:
    with pytest.raises(ValueError, match="at least one"):
        FallbackProvider([])


def test_fallback_forwards_tools_and_delta() -> None:
    ok = _ok_provider()
    fallback = FallbackProvider([ok])
    tools = [{"type": "function", "function": {"name": "calc"}}]
    delta = MagicMock()
    fallback.chat([{"role": "user", "content": "hi"}], tools=tools, on_delta=delta)
    # tools pass through verbatim; on_delta is WRAPPED (so fallback can tell whether the
    # provider streamed anything), but the wrapper forwards to the original callback.
    ok.chat.assert_called_once()
    call = ok.chat.call_args
    assert call.args[0] == [{"role": "user", "content": "hi"}]
    assert call.kwargs["tools"] == tools
    forwarded = call.kwargs["on_delta"]
    assert forwarded is not None and forwarded is not delta
    forwarded("tok")
    delta.assert_called_once_with("tok")
