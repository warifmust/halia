"""Tests for grep_file and jq_query skills."""

from __future__ import annotations

import json
from pathlib import Path

from halia.skills.grep import GrepFile
from halia.skills.jq import JqQuery

# ── grep_file tests ──────────────────────────────────────────────────────────


def test_grep_finds_literal_match(tmp_path: Path) -> None:
    f = tmp_path / "data.txt"
    f.write_text("hello world\nfoo bar\nhello again\n")
    result = GrepFile().run({"path": str(f), "query": "hello"})
    assert "2 match" in result
    assert "1:hello world" in result
    assert "3:hello again" in result


def test_grep_no_matches(tmp_path: Path) -> None:
    f = tmp_path / "data.txt"
    f.write_text("hello world\n")
    result = GrepFile().run({"path": str(f), "query": "xyz"})
    assert "no matches" in result


def test_grep_regex(tmp_path: Path) -> None:
    f = tmp_path / "data.txt"
    f.write_text("foo123\nbar456\nbaz\n")
    result = GrepFile().run({"path": str(f), "query": r"\d+", "regex": True})
    assert "2 match" in result


def test_grep_case_insensitive(tmp_path: Path) -> None:
    f = tmp_path / "data.txt"
    f.write_text("Hello\nhello\nHELLO\n")
    result = GrepFile().run({"path": str(f), "query": "hello", "ignore_case": True})
    assert "3 match" in result


def test_grep_context_lines(tmp_path: Path) -> None:
    f = tmp_path / "data.txt"
    f.write_text("line1\nline2\nline3 TARGET line4\nline5\nline6\n")
    result = GrepFile().run({"path": str(f), "query": "TARGET", "context": 1})
    assert "line2" in result  # before
    assert "line4" in result  # after


def test_grep_missing_file() -> None:
    result = GrepFile().run({"path": "/nonexistent/file.txt", "query": "x"})
    assert "error" in result


def test_grep_empty_query() -> None:
    result = GrepFile().run({"path": "/dev/null", "query": ""})
    assert "error" in result


# ── jq_query tests ───────────────────────────────────────────────────────────


def test_jq_simple_field(tmp_path: Path) -> None:
    f = tmp_path / "config.json"
    f.write_text(json.dumps({"name": "halia", "version": "0.6.0"}))
    result = JqQuery().run({"path": str(f), "query": ".name"})
    assert result == '"halia"'


def test_jq_nested_field(tmp_path: Path) -> None:
    f = tmp_path / "config.json"
    f.write_text(json.dumps({"db": {"host": "localhost", "port": 5432}}))
    result = JqQuery().run({"path": str(f), "query": ".db.host"})
    assert result == '"localhost"'


def test_jq_array_index(tmp_path: Path) -> None:
    f = tmp_path / "data.json"
    f.write_text(json.dumps({"items": [10, 20, 30]}))
    result = JqQuery().run({"path": str(f), "query": ".items[0]"})
    assert result == "10"


def test_jq_array_iteration(tmp_path: Path) -> None:
    f = tmp_path / "data.json"
    f.write_text(json.dumps({"users": [{"name": "alice"}, {"name": "bob"}]}))
    result = JqQuery().run({"path": str(f), "query": ".users[]"})
    parsed = json.loads(result)
    assert len(parsed) == 2


def test_jq_length_pipe(tmp_path: Path) -> None:
    f = tmp_path / "data.json"
    f.write_text(json.dumps({"items": [1, 2, 3, 4, 5]}))
    result = JqQuery().run({"path": str(f), "query": ".items | length"})
    assert result == "5"


def test_jq_keys_pipe(tmp_path: Path) -> None:
    f = tmp_path / "config.json"
    f.write_text(json.dumps({"a": 1, "b": 2, "c": 3}))
    result = JqQuery().run({"path": str(f), "query": ". | keys"})
    parsed = json.loads(result)
    assert sorted(parsed) == ["a", "b", "c"]


def test_jq_keys_sorted_like_jq(tmp_path: Path) -> None:
    # jq's `keys` returns SORTED keys, regardless of insertion order.
    f = tmp_path / "obj.json"
    f.write_text(json.dumps({"currency": 1, "fiscal_start_month": 2, "compliance": 3}))
    result = JqQuery().run({"path": str(f), "query": ". | keys"})
    assert json.loads(result) == ["compliance", "currency", "fiscal_start_month"]


def test_jq_keys_unsorted_preserves_insertion_order(tmp_path: Path) -> None:
    f = tmp_path / "obj.json"
    f.write_text(json.dumps({"currency": 1, "fiscal_start_month": 2, "compliance": 3}))
    result = JqQuery().run({"path": str(f), "query": ". | keys_unsorted"})
    assert json.loads(result) == ["currency", "fiscal_start_month", "compliance"]


def test_jq_filter(tmp_path: Path) -> None:
    f = tmp_path / "users.json"
    f.write_text(json.dumps([
        {"name": "alice", "age": 30},
        {"name": "bob", "age": 25},
        {"name": "carol", "age": 35},
    ]))
    result = JqQuery().run({"path": str(f), "query": ".[?age > 30]"})
    parsed = json.loads(result)
    assert len(parsed) == 1
    assert parsed[0]["name"] == "carol"


def test_jq_filter_skips_items_missing_field(tmp_path: Path) -> None:
    # Regression: a filter over a list where some items lack the field must SKIP
    # those items (jq semantics), not error the whole query.
    f = tmp_path / "users.json"
    f.write_text(json.dumps([
        {"name": "alice", "age": 30},
        {"name": "bob"},               # no 'age'
        {"name": "carol", "age": 40},
    ]))
    result = JqQuery().run({"path": str(f), "query": ".[?age > 30]"})
    assert "error" not in result
    parsed = json.loads(result)
    assert [u["name"] for u in parsed] == ["carol"]


def test_jq_filter_incomparable_types_skip(tmp_path: Path) -> None:
    # Regression: a str-vs-int comparison on one row must skip it, not crash the query.
    f = tmp_path / "mixed.json"
    f.write_text(json.dumps([
        {"name": "a", "age": 30},
        {"name": "b", "age": "unknown"},
    ]))
    result = JqQuery().run({"path": str(f), "query": ".[?age > 20]"})
    assert "error" not in result
    parsed = json.loads(result)
    assert [u["name"] for u in parsed] == ["a"]


def test_jq_invalid_json(tmp_path: Path) -> None:
    f = tmp_path / "bad.json"
    f.write_text("not json {{{")
    result = JqQuery().run({"path": str(f), "query": ".name"})
    assert "error" in result


def test_jq_missing_field(tmp_path: Path) -> None:
    f = tmp_path / "data.json"
    f.write_text(json.dumps({"name": "halia"}))
    result = JqQuery().run({"path": str(f), "query": ".missing"})
    assert "error" in result


def test_jq_truncation(tmp_path: Path) -> None:
    f = tmp_path / "big.json"
    f.write_text(json.dumps({"data": "x" * 10000}))
    result = JqQuery().run({"path": str(f), "query": ".", "max_chars": 100})
    assert "truncated" in result


def test_skills_registered() -> None:
    """Both new skills are registered in the catalogue."""
    from halia.skills import available_skills
    names = available_skills()
    assert "grep_file" in names
    assert "jq_query" in names
