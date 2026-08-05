"""Tests for the ask_user skill (pause + ask the human for gated data)."""

from typing import Any

from halia.skills.ask import AskUser


def test_requires_question() -> None:
    assert "question" in AskUser().run({})
    assert "question" in AskUser().run({"question": "   "})


def test_non_interactive_returns_unavailable(monkeypatch: Any) -> None:
    # No TTY (piped/scheduled) → it must not hang; returns a note the agent can act on.
    import sys

    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    out = AskUser().run({"question": "paste a token"})
    assert "unavailable" in out
    assert "flag it for a human" in out


def test_reads_answer_when_interactive(monkeypatch: Any) -> None:
    import sys

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("halia.skills.ask.pt_prompt", lambda *a, **kw: "Bearer abc123")
    out = AskUser().run({"question": "token?"})
    assert out == "user answered: Bearer abc123"


def test_skip_answer(monkeypatch: Any) -> None:
    import sys

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("halia.skills.ask.pt_prompt", lambda *a, **kw: "skip")
    assert "skip" in AskUser().run({"question": "blacklisted user id?"}).lower()


def test_empty_answer(monkeypatch: Any) -> None:
    import sys

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("halia.skills.ask.pt_prompt", lambda *a, **kw: "  ")
    assert "no answer" in AskUser().run({"question": "x?"})


def test_secret_uses_getpass(monkeypatch: Any) -> None:
    import sys

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("halia.skills.ask.pt_prompt", lambda *a, **kw: "s3cret")
    out = AskUser().run({"question": "token?", "secret": True})
    assert out == "user answered: s3cret"


def test_safe_and_wired() -> None:
    from halia.presets import get_preset
    from halia.skills import DEFAULT_SKILLS, available_skills, default_registry

    assert AskUser().dangerous is False
    assert "ask_user" in available_skills()
    assert "ask_user" in DEFAULT_SKILLS  # available in any chat
    assert default_registry().get("ask_user") is not None
    qa = get_preset("qa")
    assert qa is not None and "ask_user" in qa.skills
