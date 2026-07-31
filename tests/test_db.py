"""Tests for the read-only query_db skill."""

import sqlite3
from typing import Any

from halia.skills.db import QueryDb


def _make_db(tmp_path: Any) -> Any:
    db = tmp_path / "test.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE txn (id INTEGER, category TEXT, amount REAL)")
    conn.executemany(
        "INSERT INTO txn VALUES (?, ?, ?)",
        [(1, "Fuel", 60.0), (2, "Food", 45.5), (3, "Fuel", 30.0)],
    )
    conn.commit()
    conn.close()
    return db


def test_query_select(tmp_path: Any) -> None:
    out = QueryDb().run(
        {"path": str(_make_db(tmp_path)), "query": "SELECT category, amount FROM txn ORDER BY id"}
    )
    assert "category | amount" in out
    assert "Fuel" in out
    assert "45.5" in out


def test_query_aggregate(tmp_path: Any) -> None:
    out = QueryDb().run(
        {"path": str(_make_db(tmp_path)), "query": "SELECT COUNT(*) FROM txn WHERE category='Fuel'"}
    )
    assert "2" in out


def test_query_rejects_write_by_text(tmp_path: Any) -> None:
    db = _make_db(tmp_path)
    out = QueryDb().run({"path": str(db), "query": "DROP TABLE txn"})
    assert "read-only" in out.lower()
    # table survives
    conn = sqlite3.connect(db)
    remaining = conn.execute("SELECT COUNT(*) FROM txn").fetchone()[0]
    conn.close()
    assert remaining == 3


def test_query_ro_connection_blocks_write(tmp_path: Any) -> None:
    # Passes the text check (starts with WITH) but attempts a write → RO engine rejects.
    db = _make_db(tmp_path)
    out = QueryDb().run(
        {"path": str(db), "query": "WITH x AS (SELECT 1) DELETE FROM txn"}
    )
    assert "error" in out.lower()
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM txn").fetchone()[0] == 3
    conn.close()


def test_query_missing_file(tmp_path: Any) -> None:
    out = QueryDb().run({"path": str(tmp_path / "nope.db"), "query": "SELECT 1"})
    assert "not a file" in out


def test_query_requires_args() -> None:
    assert "required" in QueryDb().run({"query": "SELECT 1"})
    assert "required" in QueryDb().run({"path": "x.db"})


def test_query_db_is_safe() -> None:
    assert QueryDb().dangerous is False
