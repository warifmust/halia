"""Tests for the make_chart skill (dependency-free SVG bar chart)."""

from typing import Any

from halia.skills.chart import MakeChart, parse_chart_block, render_bar_svg


def test_parse_chart_block_single_series() -> None:
    spec = parse_chart_block("title: Averages\nAisha: 78.3\nChong: 90\nDan: 58.3")
    assert spec is not None
    assert spec.kind == "bar"  # default
    assert spec.title == "Averages"
    assert spec.categories == ["Aisha", "Chong", "Dan"]
    assert len(spec.series) == 1 and spec.series[0][1] == [78.3, 90.0, 58.3]


def test_parse_chart_block_multi_series() -> None:
    spec = parse_chart_block(
        "type: line\ntitle: Sales\nx: Jan, Feb, Mar\nNorth: 100, 140, 120\nSouth: 80, 95, 110"
    )
    assert spec is not None
    assert spec.kind == "line"
    assert spec.categories == ["Jan", "Feb", "Mar"]
    assert spec.series == [("North", [100.0, 140.0, 120.0]), ("South", [80.0, 95.0, 110.0])]


def test_parse_chart_block_pie() -> None:
    spec = parse_chart_block("type: pie\ntitle: Share\nNorth: 60\nSouth: 40")
    assert spec is not None
    assert spec.kind == "pie"
    assert spec.categories == ["North", "South"]


def test_parse_chart_block_scatter() -> None:
    spec = parse_chart_block("type: scatter\nxlabel: price\nylabel: units\n10, 200\n12, 180")
    assert spec is not None
    assert spec.kind == "scatter"
    assert spec.points == [(10.0, 200.0), (12.0, 180.0)]
    assert spec.x_label == "price" and spec.y_label == "units"


def test_histogram_bins() -> None:
    from halia.skills.chart import histogram_bins

    labels, counts = histogram_bins([1, 2, 2, 3, 3, 3, 4, 5, 9, 10], 5)
    assert len(labels) == 5 and len(counts) == 5
    assert sum(counts) == 10  # every value counted once


def test_parse_chart_block_area() -> None:
    spec = parse_chart_block("type: area\ntitle: Cumulative\nJan: 10\nFeb: 25\nMar: 45")
    assert spec is not None
    assert spec.kind == "area"
    assert spec.categories == ["Jan", "Feb", "Mar"]


def test_parse_chart_block_histogram() -> None:
    spec = parse_chart_block("type: histogram\nbins: 4\nvalues: 55, 62, 71, 78, 85, 90, 92")
    assert spec is not None
    assert spec.kind == "histogram"
    assert len(spec.categories) == 4  # 4 bins
    assert sum(spec.series[0][1]) == 7  # all 7 values binned


def test_area_svg_has_fill_polygon() -> None:
    from halia.skills.chart import render_chart_svg

    spec = parse_chart_block("type: area\nA: 10\nB: 20\nC: 15")
    assert spec is not None
    assert "<polygon" in render_chart_svg(spec)  # filled area under the line


def test_make_chart_histogram(tmp_path: Any) -> None:
    out = tmp_path / "h.svg"
    r = MakeChart().run(
        {"path": str(out), "kind": "histogram", "values": [1, 2, 3, 4, 5, 6, 7, 8], "bins": 4}
    )
    assert "wrote histogram chart" in r
    assert out.read_text().count("<rect") == 4  # one bar per bin


def test_parse_chart_block_type_line() -> None:
    spec = parse_chart_block("type: line\ntitle: Trend\nJan: 10\nFeb: 14\nMar: 12")
    assert spec is not None
    assert spec.kind == "line"
    assert spec.categories == ["Jan", "Feb", "Mar"]


def test_parse_chart_block_title_optional_and_money() -> None:
    spec = parse_chart_block("Q1: $1,200\nQ2: $980")
    assert spec is not None
    assert spec.kind == "bar"  # default kind
    assert spec.title == ""  # no title
    assert spec.series[0][1] == [1200.0, 980.0]  # single series, $ and comma stripped


def test_parse_chart_block_empty_is_none() -> None:
    assert parse_chart_block("title: nothing here\njust prose") is None


def test_render_line_svg_has_polyline() -> None:
    from halia.skills.chart import render_line_svg

    svg = render_line_svg("Trend", ["Jan", "Feb", "Mar"], [10.0, 14.0, 12.0])
    assert svg.startswith("<svg")
    assert "<polyline" in svg  # the connecting line
    assert "Trend" in svg


def test_make_chart_line_kind(tmp_path: Any) -> None:
    out = tmp_path / "trend.svg"
    r = MakeChart().run(
        {"path": str(out), "labels": ["Jan", "Feb"], "values": [10, 14], "kind": "line"}
    )
    assert "wrote line chart" in r
    assert "<polyline" in out.read_text()


def test_make_chart_multi_series(tmp_path: Any) -> None:
    out = tmp_path / "multi.svg"
    r = MakeChart().run(
        {
            "path": str(out), "labels": ["Jan", "Feb", "Mar"], "kind": "bar",
            "series": [
                {"name": "North", "values": [100, 140, 120]},
                {"name": "South", "values": [80, 95, 110]},
            ],
        }
    )
    assert "wrote bar chart" in r
    svg = out.read_text()
    assert "North" in svg and "South" in svg  # legend
    assert svg.count("<rect") >= 6  # 2 series × 3 categories = 6 bars (+ legend swatches)


def test_make_chart_multi_line_svg() -> None:
    from halia.skills.chart import render_multi_svg

    svg = render_multi_svg(
        "line", "Trend", ["Q1", "Q2"], [("A", [1.0, 2.0]), ("B", [3.0, 1.0])]
    )
    assert svg.count("<polyline") == 2  # one line per series


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
    assert "wrote bar chart" in result
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
    assert "labels" in r


def test_make_chart_pie(tmp_path: Any) -> None:
    out = tmp_path / "pie.svg"
    r = MakeChart().run(
        {"path": str(out), "kind": "pie", "labels": ["North", "South"], "values": [60, 40]}
    )
    assert "wrote pie chart" in r
    svg = out.read_text()
    assert "<path" in svg  # pie slice(s)
    assert "60%" in svg and "40%" in svg  # legend percentages


def test_make_chart_scatter(tmp_path: Any) -> None:
    out = tmp_path / "scatter.svg"
    r = MakeChart().run(
        {"path": str(out), "kind": "scatter", "points": [[10, 200], [12, 180], [15, 260]],
         "xlabel": "price", "ylabel": "units"}
    )
    assert "wrote scatter chart" in r
    svg = out.read_text()
    assert svg.count("<circle") == 3  # one point per pair
    assert "price" in svg


def test_make_chart_scatter_needs_points(tmp_path: Any) -> None:
    r = MakeChart().run({"path": str(tmp_path / "s.svg"), "kind": "scatter"})
    assert "points" in r


def test_pie_and_scatter_embed_in_pdf(tmp_path: Any) -> None:
    from halia.skills.export import render_markdown_pdf

    md = (
        "# Charts\n\n```chart\ntype: pie\ntitle: Share\nA: 60\nB: 40\n```\n\n"
        "```chart\ntype: scatter\nxlabel: x\nylabel: y\n1, 2\n3, 5\n4, 4\n```\n"
    )
    out = tmp_path / "charts.pdf"
    render_markdown_pdf(md).output(str(out))
    assert out.read_bytes().startswith(b"%PDF")


def test_make_chart_honors_permission_floor(tmp_path: Any) -> None:
    # a sensitive filename is blocked by the floor, even for a chart
    blocked = tmp_path / "id_rsa.svg"
    r = MakeChart().run({"path": str(blocked), "labels": ["a"], "values": [1]})
    assert r.startswith("blocked:")
    assert not blocked.exists()


def test_make_chart_is_dangerous() -> None:
    assert MakeChart().dangerous is True  # writes a file → gated
