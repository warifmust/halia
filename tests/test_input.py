"""Tests for halia.cli.input shared helpers."""

from __future__ import annotations

import sys
from typing import Any

from halia.cli import input as input_mod


def test_ask_returns_prompt_input(monkeypatch: Any) -> None:
    """ask() delegates to prompt_toolkit prompt and returns the answer."""
    calls: list[tuple[Any, Any]] = []

    def fake_prompt(*args: Any, **kwargs: Any) -> str:
        calls.append((args, kwargs))
        return "hello"

    monkeypatch.setattr(input_mod, "pt_prompt", fake_prompt)
    assert input_mod.ask("you › ") == "hello"
    assert calls[0][0][0] == [("class:prompt", "you › ")]


def test_ask_password(monkeypatch: Any) -> None:
    """ask() forwards is_password to prompt_toolkit."""
    calls: list[dict[str, Any]] = []

    def fake_prompt(*args: Any, **kwargs: Any) -> str:
        calls.append(kwargs)
        return "secret"

    monkeypatch.setattr(input_mod, "pt_prompt", fake_prompt)
    assert input_mod.ask("key: ", is_password=True) == "secret"
    assert calls[0]["is_password"] is True


def test_pick_fallback_when_not_tty(monkeypatch: Any) -> None:
    """pick() falls back to a numbered list when stdin is not a TTY."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(
        input_mod, "ask",
        lambda prompt_text="", default="", is_password=False: "2",
    )
    # User enters "2", which maps to option index 1.
    assert input_mod.pick("Choose:", ["a", "b", "c"]) == "b"


def test_pick_empty_options() -> None:
    """pick() returns an empty string when given no options."""
    assert input_mod.pick("Choose:", []) == ""


def test_pick_default_bounds(monkeypatch: Any) -> None:
    """pick() clamps an out-of-range default index and uses it as fallback default."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    # Simulate the user pressing Enter to accept the default.
    monkeypatch.setattr(
        input_mod, "ask",
        lambda prompt_text="", default="", is_password=False: default,
    )
    # default=5 is clamped to last index (2), so the default shown is "3".
    assert input_mod.pick("Choose:", ["a", "b", "c"], default=5) == "c"
