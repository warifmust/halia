"""Tests for the read_csv skill."""

from typing import Any

import pytest

from halia.permissions.guard import PermissionDenied
from halia.skills.data import AggregateCsv, ReadCsv


def _write_txn(tmp_path: Any) -> Any:
    csv_file = tmp_path / "t.csv"
    csv_file.write_text(
        "date,description,amount\n"
        "2026-07-01,Groceries,45.50\n"
        "2026-07-03,Fuel,60.00\n"
        "2026-07-05,Coffee,4.25\n"
        "2026-07-08,Dinner,32.75\n"
        "2026-07-10,Books,18.00\n"
    )
    return csv_file


def test_aggregate_sum_is_exact(tmp_path: Any) -> None:
    out = AggregateCsv().run(
        {"path": str(_write_txn(tmp_path)), "column": "amount", "operation": "sum"}
    )
    assert "160.50" in out  # exact decimal, no float drift


def test_aggregate_mean_min_max(tmp_path: Any) -> None:
    csv_file = str(_write_txn(tmp_path))
    assert "32.1" in AggregateCsv().run({"path": csv_file, "column": "amount", "operation": "mean"})
    assert "4.25" in AggregateCsv().run({"path": csv_file, "column": "amount", "operation": "min"})
    assert "60.00" in AggregateCsv().run({"path": csv_file, "column": "amount", "operation": "max"})


def test_aggregate_count(tmp_path: Any) -> None:
    out = AggregateCsv().run(
        {"path": str(_write_txn(tmp_path)), "column": "amount", "operation": "count"}
    )
    assert "5" in out


def test_aggregate_missing_column(tmp_path: Any) -> None:
    out = AggregateCsv().run(
        {"path": str(_write_txn(tmp_path)), "column": "nope", "operation": "sum"}
    )
    assert "not found" in out


def test_aggregate_bad_operation(tmp_path: Any) -> None:
    out = AggregateCsv().run(
        {"path": str(_write_txn(tmp_path)), "column": "amount", "operation": "median"}
    )
    assert "operation" in out


def test_aggregate_skips_non_numeric(tmp_path: Any) -> None:
    csv_file = tmp_path / "mixed.csv"
    csv_file.write_text("amount\n10\nN/A\n20\n")
    out = AggregateCsv().run({"path": str(csv_file), "column": "amount", "operation": "sum"})
    assert "30" in out
    assert "skipped" in out


def test_aggregate_is_safe() -> None:
    assert AggregateCsv().dangerous is False


def test_read_csv(tmp_path: Any) -> None:
    csv_file = tmp_path / "data.csv"
    csv_file.write_text("name,age\nAlice,30\nBob,25\n")
    out = ReadCsv().run({"path": str(csv_file)})
    assert "columns (2): name, age" in out
    assert "data rows: 2" in out
    assert "Alice | 30" in out
    assert "Bob | 25" in out


def test_read_csv_sample_limit(tmp_path: Any) -> None:
    csv_file = tmp_path / "big.csv"
    csv_file.write_text("col\n" + "\n".join(str(i) for i in range(100)) + "\n")
    out = ReadCsv().run({"path": str(csv_file), "sample_rows": 3})
    assert "data rows: 100" in out
    # header line + "sample:" + header + 3 sampled rows → the "N | " data lines are bounded
    assert out.count("\n") <= 6


def test_read_csv_missing(tmp_path: Any) -> None:
    assert "not a file" in ReadCsv().run({"path": str(tmp_path / "nope.csv")})


def test_read_csv_requires_path() -> None:
    assert "required" in ReadCsv().run({})


def test_read_csv_is_safe() -> None:
    assert ReadCsv().dangerous is False


def test_read_csv_routes_through_permission_floor(tmp_path: Any) -> None:
    sensitive = tmp_path / "config.env"  # name contains ".env" → blocked
    sensitive.write_text("x,y\n1,2\n")
    with pytest.raises(PermissionDenied):
        ReadCsv().run({"path": str(sensitive)})
