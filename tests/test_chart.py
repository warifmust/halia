"""Tests for the make_chart skill (dependency-free SVG bar chart)."""

from typing import Any

from halia.skills.chart import MakeChart, parse_chart_block, render_bar_svg


def test_parse_chart_block_reads_title_labels_values() -> None:
    spec = parse_chart_block("title: Averages\nAisha: 78.3\nChong: 90\nDan: 58.3")
    assert spec is not None
    title, labels, values = spec
    assert title == "Averages"
    assert labels == ["Aisha", "Chong", "Dan"]
    assert values == [78.3, 90.0, 58.3]


def test_parse_chart_block_title_optional_and_money() -> None:
    spec = parse_chart_block("Q1: $1,200\nQ2: $980")
    assert spec is not None
    assert spec[0] == ""  # no title
    assert spec[2] == [1200.0, 980.0]  # $ and comma stripped


def test_parse_chart_block_empty_is_none() -> None:
    assert parse_chart_block("title: nothing here\njust prose") is None


def test_render_produces_svg_with_bars() -> None:
    svg = render_bar_svg("Grades", ["A", "B", "C"], [5.0, 8.0, 3.0])
    assert svg.startswith("<svg")
    assert svg.count("<rect") == 3  # one bar per value
    assert "Grades" in svg
    assert "</svg>" in svg


def test_render_escapes_xml() -> None:
    svg = render_bar_svg("Pass & Fail <7", ["<x>", "y&z"], [1.0, 2.0])
    assert "&amp;" in svg and "&lt;" in svg
    assert "<x>" not in svg.replace("<svg", "")  # the label < is escaped, not raw


def test_make_chart_writes_file(tmp_path: Any) -> None:
    out = tmp_path / "chart.svg"
    result = MakeChart().run(
        {"path": str(out), "labels": ["Term1", "Term2"], "values": [72, 81], "title": "Scores"}
    )
    assert "wrote bar chart (2 bars)" in result
    assert out.read_text().startswith("<svg")


def test_make_chart_validates_lengths(tmp_path: Any) -> None:
    out = tmp_path / "c.svg"
    r = MakeChart().run({"path": str(out), "labels": ["a", "b"], "values": [1]})
    assert "same length" in r
    assert not out.exists()


def test_make_chart_rejects_non_numeric(tmp_path: Any) -> None:
    r = MakeChart().run(
        {"path": str(tmp_path / "c.svg"), "labels": ["a"], "values": ["oops"]}
    )
    assert "must be a number" in r


def test_make_chart_requires_labels(tmp_path: Any) -> None:
    r = MakeChart().run({"path": str(tmp_path / "c.svg"), "labels": [], "values": []})
    assert "non-empty" in r


def test_make_chart_honors_permission_floor(tmp_path: Any) -> None:
    # a sensitive filename is blocked by the floor, even for a chart
    blocked = tmp_path / "id_rsa.svg"
    r = MakeChart().run({"path": str(blocked), "labels": ["a"], "values": [1]})
    assert r.startswith("blocked:")
    assert not blocked.exists()


def test_make_chart_is_dangerous() -> None:
    assert MakeChart().dangerous is True  # writes a file → gated
