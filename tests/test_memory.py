"""Tests for user-controlled memory."""

from typing import Any

from halia.memory.facts import forget, list_facts, memory_block, remember


def test_remember_and_list(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    remember("base currency is MYR", db_path=db)
    remember("company is Acme", db_path=db)
    facts = list_facts(db_path=db)
    assert len(facts) == 2
    contents = {f.content for f in facts}
    assert contents == {"base currency is MYR", "company is Acme"}


def test_forget(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    fact = remember("temporary note", db_path=db)
    assert forget(fact.id, db_path=db) is True
    assert list_facts(db_path=db) == []
    # forgetting again is a no-op
    assert forget(fact.id, db_path=db) is False


def test_forget_by_prefix(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    fact = remember("note", db_path=db)
    assert forget(fact.id[:4], db_path=db) is True
    assert list_facts(db_path=db) == []


def test_memory_block_empty(tmp_path: Any) -> None:
    assert memory_block(db_path=tmp_path / "halia.db") == ""


def test_memory_block_renders_facts(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    remember("base currency is MYR", db_path=db)
    block = memory_block(db_path=db)
    assert "remember" in block.lower()
    assert "- base currency is MYR" in block
