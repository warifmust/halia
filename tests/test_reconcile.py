"""Tests for the reconcile_csv skill."""

from typing import Any

from halia.skills.reconcile import ReconcileCsv


def _write(path: Any, text: str) -> str:
    path.write_text(text)
    return str(path)


def test_reconcile_matches_and_differences(tmp_path: Any) -> None:
    left = _write(
        tmp_path / "ledger.csv",
        "id,amount\nT1,100.00\nT2,200.00\nT3,50.00\n",
    )
    right = _write(
        tmp_path / "bank.csv",
        "id,amount\nT1,100.00\nT2,205.00\nT4,75.00\n",  # T2 differs, T3 missing, T4 extra
    )
    out = ReconcileCsv().run({"left": left, "right": right, "key": "id", "value": "amount"})
    assert "matched keys: 2" in out  # T1, T2
    assert "only in ledger.csv: 1" in out and "T3" in out
    assert "only in bank.csv: 1" in out and "T4" in out
    assert "value 'amount' mismatches: 1" in out
    assert "T2: 200.00 vs 205.00" in out


def test_reconcile_decimal_exact(tmp_path: Any) -> None:
    # 100.00 vs 100.0 are equal as decimals — not a mismatch.
    left = _write(tmp_path / "a.csv", "id,amount\nX,100.00\n")
    right = _write(tmp_path / "b.csv", "id,amount\nX,100.0\n")
    out = ReconcileCsv().run({"left": left, "right": right, "key": "id", "value": "amount"})
    assert "value 'amount' mismatches: 0" in out


def test_reconcile_without_value_column(tmp_path: Any) -> None:
    left = _write(tmp_path / "a.csv", "id\nA\nB\n")
    right = _write(tmp_path / "b.csv", "id\nB\nC\n")
    out = ReconcileCsv().run({"left": left, "right": right, "key": "id"})
    assert "matched keys: 1" in out
    assert "mismatches" not in out  # no value column → no value section


def test_reconcile_missing_key_column(tmp_path: Any) -> None:
    left = _write(tmp_path / "a.csv", "id\nA\n")
    right = _write(tmp_path / "b.csv", "id\nA\n")
    out = ReconcileCsv().run({"left": left, "right": right, "key": "nope"})
    assert "not in" in out


def test_reconcile_requires_args() -> None:
    assert "required" in ReconcileCsv().run({"right": "b.csv", "key": "id"})
    assert "required" in ReconcileCsv().run({"left": "a.csv", "key": "id"})
    assert "required" in ReconcileCsv().run({"left": "a.csv", "right": "b.csv"})


def test_reconcile_is_safe() -> None:
    assert ReconcileCsv().dangerous is False
