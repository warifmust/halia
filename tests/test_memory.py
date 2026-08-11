"""Tests for user-controlled memory."""

from typing import Any

from halia.memory.facts import (
    _RECALL_THRESHOLD,
    forget,
    list_facts,
    memory_block,
    recall,
    remember,
)


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


# --- FTS5 recall (Tier 1) ---


def test_recall_ranks_relevant_facts(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    remember("the base reporting currency is MYR", db_path=db)
    remember("the office wifi password is on the whiteboard", db_path=db)
    remember("quarterly board meetings are on the first Monday", db_path=db)

    hits = recall("what currency do we report in?", db_path=db)
    assert hits, "expected an FTS match"
    assert "currency" in hits[0].content.lower()  # the currency fact ranks first
    assert not any("wifi" in h.content.lower() for h in hits)  # unrelated fact excluded


def test_recall_empty_on_no_match_or_junk_query(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    remember("the base currency is MYR", db_path=db)
    assert recall("xyzzy nonexistent term", db_path=db) == []
    assert recall("!!! ...", db_path=db) == []  # no usable terms → []


def test_recall_survives_fts_special_chars(tmp_path: Any) -> None:
    # A raw prompt with FTS5 metacharacters must not raise — terms are quoted/literal.
    db = tmp_path / "halia.db"
    remember("deploy uses the staging cluster", db_path=db)
    hits = recall('where does "deploy" run? (staging* OR prod:)', db_path=db)
    assert any("staging" in h.content.lower() for h in hits)


def test_memory_block_small_set_dumps_all(tmp_path: Any) -> None:
    # At/below the threshold, behaviour is unchanged: every fact is injected, query ignored.
    db = tmp_path / "halia.db"
    for i in range(5):
        remember(f"fact number {i}", db_path=db)
    block = memory_block(query="anything", db_path=db)
    for i in range(5):
        assert f"- fact number {i}" in block


def test_memory_block_large_set_recalls_relevant(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    for i in range(_RECALL_THRESHOLD + 5):
        remember(f"unrelated filler note {i}", db_path=db)
    remember("the API base url is example.internal", db_path=db)

    block = memory_block(query="what is the api base url?", db_path=db)
    assert "example.internal" in block  # the relevant fact surfaced
    # bounded — not every filler note is dumped
    assert block.count("- ") <= 8 + 1
