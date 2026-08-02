"""Tests for clean_csv (transform-and-save with an auditable report)."""

from typing import Any

from halia.skills.clean import CleanCsv

_MESSY = (
    "name,region,joined,amount\n"
    "  Ali ,north,2026-01-15,1200\n"
    "Bea,NORTH,15/02/2026,800\n"
    "Ali ,North,2026-01-15,1200\n"  # duplicate of row 1 after trim
    "Cho,south,03 Mar 2026,\n"
    "Dan,South,,600\n"
)


def _write(tmp_path: Any) -> str:
    p = tmp_path / "messy.csv"
    p.write_text(_MESSY)
    return str(p)


def _clean(tmp_path: Any, ops: list[dict[str, Any]]) -> tuple[str, str]:
    out = tmp_path / "clean.csv"
    report = CleanCsv().run({"path": _write(tmp_path), "output": str(out), "operations": ops})
    return report, out.read_text()


def test_trim_and_case(tmp_path: Any) -> None:
    report, text = _clean(
        tmp_path, [{"op": "trim", "column": "name"}, {"op": "titlecase", "column": "region"}]
    )
    assert "trim(name): 2 cells trimmed" in report
    assert "titlecase(region): 3 cells changed" in report
    assert "Ali,North," in text  # trimmed + cased


def test_standardize_date(tmp_path: Any) -> None:
    _, text = _clean(tmp_path, [{"op": "standardize_date", "column": "joined"}])
    assert "2026-02-15" in text  # 15/02/2026 (day-first) -> ISO
    assert "2026-03-03" in text  # "03 Mar 2026" -> ISO


def test_standardize_date_explicit_format(tmp_path: Any) -> None:
    p = tmp_path / "d.csv"
    p.write_text("d\n01/02/2026\n")  # ambiguous — explicit format disambiguates
    out = tmp_path / "o.csv"
    CleanCsv().run(
        {
            "path": str(p), "output": str(out),
            "operations": [{"op": "standardize_date", "column": "d", "from": "%m/%d/%Y"}],
        }
    )
    assert "2026-01-02" in out.read_text()  # m/d -> Jan 2


def test_fill_blank_and_drop_duplicates(tmp_path: Any) -> None:
    report, text = _clean(
        tmp_path,
        [
            {"op": "trim", "column": "name"},
            {"op": "titlecase", "column": "region"},  # so row 1 & 3 actually match
            {"op": "fill_blank", "column": "amount", "value": "0"},
            {"op": "drop_duplicates"},
        ],
    )
    assert "fill_blank(amount): 1 blanks filled" in report
    assert "drop_duplicates: 1 rows removed" in report
    assert "rows: 5 → 4" in report


def test_drop_missing(tmp_path: Any) -> None:
    report, _ = _clean(tmp_path, [{"op": "drop_missing", "column": "joined"}])
    assert "drop_missing(joined): 1 rows removed" in report  # Dan has blank joined


def test_replace_and_rename(tmp_path: Any) -> None:
    report, text = _clean(
        tmp_path,
        [
            {"op": "replace", "column": "region", "map": {"north": "N", "NORTH": "N"}},
            {"op": "rename", "column": "amount", "to": "sales"},
        ],
    )
    assert "replace(region): 2 cells remapped" in report
    assert text.splitlines()[0] == "name,region,joined,sales"  # header renamed


def test_unknown_column_is_reported_not_fatal(tmp_path: Any) -> None:
    report, _ = _clean(tmp_path, [{"op": "trim", "column": "nope"}])
    assert "column 'nope' not found" in report


def test_validation(tmp_path: Any) -> None:
    assert "operations" in CleanCsv().run(
        {"path": _write(tmp_path), "output": str(tmp_path / "o.csv"), "operations": []}
    )
    assert "output" in CleanCsv().run({"path": _write(tmp_path), "output": "", "operations": [{}]})


def test_honors_permission_floor(tmp_path: Any) -> None:
    r = CleanCsv().run(
        {"path": _write(tmp_path), "output": str(tmp_path / "id_rsa.csv"),
         "operations": [{"op": "trim", "column": "name"}]}
    )
    assert r.startswith("blocked:")


def test_dangerous_and_wired() -> None:
    from halia.presets import get_preset
    from halia.skills import available_skills

    assert CleanCsv().dangerous is True
    assert "clean_csv" in available_skills()
    assert "clean_csv" in get_preset("data").skills
