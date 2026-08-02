"""Tests for query_data (SQL over CSV/Excel files via in-memory SQLite)."""

from typing import Any

from halia.skills.db import QueryData


def _orders(tmp_path: Any) -> str:
    p = tmp_path / "orders.csv"
    p.write_text("id,rep_id,amount\n1,10,1200\n2,11,800\n3,10,1500\n4,12,2000\n")
    return str(p)


def _reps(tmp_path: Any) -> str:
    p = tmp_path / "reps.csv"
    p.write_text("rep_id,name,region\n10,Ali,North\n11,Bea,South\n12,Cho,West\n")
    return str(p)


def test_group_and_order(tmp_path: Any) -> None:
    out = QueryData().run(
        {
            "files": [_orders(tmp_path)],
            "query": "SELECT rep_id, SUM(amount) t FROM orders GROUP BY rep_id ORDER BY t DESC",
        }
    )
    lines = out.splitlines()
    assert lines[0] == "rep_id | t"
    assert lines[1] == "10 | 2700"  # numeric typing → SUM + DESC work


def test_join_across_two_files(tmp_path: Any) -> None:
    out = QueryData().run(
        {
            "files": [_orders(tmp_path), _reps(tmp_path)],
            "query": (
                "SELECT r.name, SUM(o.amount) t FROM orders o "
                "JOIN reps r ON o.rep_id = r.rep_id GROUP BY r.name ORDER BY t DESC"
            ),
        }
    )
    assert "Ali | 2700" in out
    assert out.splitlines()[1].startswith("Ali")  # top by total


def test_quoted_column_with_space(tmp_path: Any) -> None:
    p = tmp_path / "sales report.csv"
    p.write_text("Sales Rep,Deal Size\nAli,1200\nBea,800\n")
    query = 'SELECT "Sales Rep" FROM "sales_report" ORDER BY "Deal Size" DESC'
    out = QueryData().run({"files": [str(p)], "query": query})
    assert "Ali" in out.splitlines()[1]


def test_read_only_rejects_writes(tmp_path: Any) -> None:
    writes = ["DROP TABLE orders", "INSERT INTO orders VALUES (9,9,9)", "UPDATE orders SET x=0"]
    for q in writes:
        out = QueryData().run({"files": [_orders(tmp_path)], "query": q})
        assert "only read-only" in out


def test_unknown_table_lists_available(tmp_path: Any) -> None:
    out = QueryData().run({"files": [_orders(tmp_path)], "query": "SELECT * FROM nope"})
    assert "no such table" in out
    assert "orders(id, rep_id, amount)" in out  # self-correcting hint


def test_excel_file(tmp_path: Any) -> None:
    from openpyxl import Workbook

    path = tmp_path / "book.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["city", "sales"])
    ws.append(["KL", 500])
    ws.append(["JB", 300])
    wb.save(path)
    out = QueryData().run(
        {"files": [str(path)], "query": "SELECT SUM(sales) s FROM book"}
    )
    assert out.splitlines()[1] == "800"


def test_validation(tmp_path: Any) -> None:
    assert "files" in QueryData().run({"files": [], "query": "SELECT 1"})
    assert "query" in QueryData().run({"files": [_orders(tmp_path)], "query": " "})


def test_unsupported_file_type(tmp_path: Any) -> None:
    p = tmp_path / "data.json"
    p.write_text("{}")
    out = QueryData().run({"files": [str(p)], "query": "SELECT 1"})
    assert "unsupported file type" in out


def test_query_data_safe_and_wired() -> None:
    from halia.presets import get_preset
    from halia.skills import available_skills, default_registry

    assert QueryData().dangerous is False
    assert "query_data" in available_skills()
    assert default_registry().get("query_data") is not None
    assert "query_data" in get_preset("data").skills
