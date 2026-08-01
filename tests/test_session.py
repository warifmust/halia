"""Tests for session persistence (chat Tier 2 — survive a restart)."""

from dataclasses import replace
from typing import Any

from halia.core.session import (
    delete_session,
    get_session,
    list_sessions,
    new_session,
    save_session,
)
from halia.providers.base import Message


def _msgs() -> list[Message]:
    return [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "what is 2+2?"},
        {"role": "assistant", "content": "4"},
    ]


def test_session_roundtrip(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    s = new_session("deepseek", "deepseek-v4-flash", "finance", True, _msgs())
    save_session(s, db_path=db)
    loaded = get_session(s.id, db_path=db)
    assert loaded is not None
    assert loaded.provider == "deepseek"
    assert loaded.profile == "finance"
    assert loaded.allow_commands is True
    assert loaded.messages[1]["content"] == "what is 2+2?"
    assert loaded.turn_count() == 1  # one user turn
    assert loaded.title == "what is 2+2?"  # derived from first user message


def test_save_updates_in_place_and_grows(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    s = new_session("p", "m", None, False, _msgs())
    save_session(s, db_path=db)
    # a new turn appended, same id → upsert, not a second row
    grown = replace(s, messages=[*_msgs(), {"role": "user", "content": "and 3+3?"}])
    save_session(grown, db_path=db)
    all_sessions = list_sessions(db_path=db)
    assert len(all_sessions) == 1
    assert all_sessions[0].turn_count() == 2


def test_get_session_prefix_and_ambiguity(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    save_session(replace(new_session("p", "m", None, False, _msgs()), id="ab11"), db_path=db)
    save_session(replace(new_session("p", "m", None, False, _msgs()), id="ab22"), db_path=db)
    assert get_session("ab11", db_path=db) is not None  # exact
    assert get_session("ab", db_path=db) is None  # ambiguous → refuse to guess
    assert get_session("zz", db_path=db) is None  # missing


def test_delete_session(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    s = new_session("p", "m", None, False, _msgs())
    save_session(s, db_path=db)
    assert delete_session(s.id, db_path=db) is True
    assert get_session(s.id, db_path=db) is None
    assert delete_session(s.id, db_path=db) is False  # already gone


def test_list_sessions_empty(tmp_path: Any) -> None:
    assert list_sessions(db_path=tmp_path / "nope.db") == []
