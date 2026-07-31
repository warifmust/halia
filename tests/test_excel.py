"""Tests for the read_excel skill."""

from typing import Any

import openpyxl

from halia.skills.excel import ReadExcel


def _make_xlsx(tmp_path: Any) -> Any:
    path = tmp_path / "book.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Transactions"
    sheet.append(["date", "category", "amount"])
    sheet.append(["2026-07-01", "Fuel", 60.0])
    sheet.append(["2026-07-02", "Food", 45.5])
    workbook.create_sheet("Notes")
    workbook.save(path)
    return path


def test_list_sheets(tmp_path: Any) -> None:
    out = ReadExcel().run({"path": str(_make_xlsx(tmp_path))})
    assert "sheets:" in out
    assert "Transactions" in out
    assert "Notes" in out


def test_read_sheet(tmp_path: Any) -> None:
    out = ReadExcel().run({"path": str(_make_xlsx(tmp_path)), "sheet": "Transactions"})
    assert "columns (3): date, category, amount" in out
    assert "data rows: 2" in out
    assert "Fuel" in out
    assert "45.5" in out


def test_missing_sheet(tmp_path: Any) -> None:
    out = ReadExcel().run({"path": str(_make_xlsx(tmp_path)), "sheet": "Nope"})
    assert "not found" in out


def test_read_excel_requires_path() -> None:
    assert "required" in ReadExcel().run({})


def test_read_excel_not_a_file(tmp_path: Any) -> None:
    assert "not a file" in ReadExcel().run({"path": str(tmp_path / "nope.xlsx")})


def test_read_excel_is_safe() -> None:
    assert ReadExcel().dangerous is False
