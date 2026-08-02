"""Tests for group_by (the data-analysis aggregation workhorse)."""

from typing import Any

from halia.skills.data import GroupByCsv

_CSV = "region,rep,amount\nNorth,A,1200\nSouth,B,800\nNorth,C,1500\nWest,B,2000\nNorth,B,300\n"


def _write(tmp_path: Any) -> str:
    p = tmp_path / "sales.csv"
    p.write_text(_CSV)
    return str(p)


def test_group_sum_sorted_desc(tmp_path: Any) -> None:
    out = GroupByCsv().run(
        {"path": _write(tmp_path), "group_by": "region", "value": "amount", "operation": "sum"}
    )
    lines = out.splitlines()
    assert "3 groups" in lines[0]
    # North 3000, West 2000, South 800 — largest first
    assert lines[1].startswith("North | 3000")
    assert lines[2].startswith("West | 2000")
    assert lines[3].startswith("South | 800")


def test_group_count_needs_no_value(tmp_path: Any) -> None:
    out = GroupByCsv().run({"path": _write(tmp_path), "group_by": "region", "operation": "count"})
    assert "North | 3" in out
    assert "West | 1" in out


def test_group_mean_is_exact(tmp_path: Any) -> None:
    out = GroupByCsv().run(
        {"path": _write(tmp_path), "group_by": "region", "value": "amount", "operation": "mean"}
    )
    # North mean = (1200+1500+300)/3 = 1000
    assert "North | 1000" in out


def test_missing_value_column_for_non_count(tmp_path: Any) -> None:
    out = GroupByCsv().run({"path": _write(tmp_path), "group_by": "region", "operation": "sum"})
    assert "'value'" in out and "required" in out


def test_unknown_group_column(tmp_path: Any) -> None:
    out = GroupByCsv().run(
        {"path": _write(tmp_path), "group_by": "nope", "value": "amount", "operation": "sum"}
    )
    assert "not found" in out


def test_limit_caps_groups(tmp_path: Any) -> None:
    out = GroupByCsv().run(
        {"path": _write(tmp_path), "group_by": "region", "operation": "count", "limit": 2}
    )
    assert "top 2 of 3 shown" in out


def test_group_by_is_safe_and_wired() -> None:
    from halia.presets import get_preset, preset_names
    from halia.skills import available_skills

    assert GroupByCsv().dangerous is False
    assert "group_by" in available_skills()
    data = get_preset("data")
    assert data is not None and "group_by" in data.skills
    assert "data" in preset_names()
