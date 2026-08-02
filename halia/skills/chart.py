"""Chart skill — turn data into charts (bar, line, pie, scatter), saved as SVG.

Dependency-free (SVG text + fpdf2 primitives + native pptx charts — no matplotlib).
The model: bar/line/pie use `categories` × named `series`; scatter uses (x, y) `points`.
`parse_chart_block` is shared by the PDF/PPTX/DOCX renderers so a chart in the markdown
master renders natively in each format (no SVG→raster).

Writes a file, so — like write_file — it is dangerous (approval-gated) and floored.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from halia.permissions.guard import PermissionDenied, check_writable

_W, _H = 680, 420
_LEFT, _RIGHT, _TOP, _BOTTOM = 60, 20, 48, 70
_PALETTE = ["#4f7cff", "#ff6b6b", "#2ecc71", "#f1c40f", "#9b59b6", "#e67e22"]
_CHART_KINDS = ("bar", "line", "pie", "scatter", "area", "histogram")

# A named series of values aligned to the chart's categories (bar/line/pie).
Series = list[tuple[str, list[float]]]


@dataclass
class ChartSpec:
    """A parsed chart. Category charts use categories+series; scatter uses points."""

    kind: str
    title: str = ""
    categories: list[str] = field(default_factory=list)
    series: Series = field(default_factory=list)
    points: list[tuple[float, float]] = field(default_factory=list)  # scatter (x, y)
    x_label: str = ""
    y_label: str = ""


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}"


def _color(i: int) -> str:
    return _PALETTE[i % len(_PALETTE)]


def histogram_bins(values: list[float], n_bins: int) -> tuple[list[str], list[float]]:
    """Bin raw numbers into `n_bins` equal-width ranges; return (bin labels, counts)."""
    n_bins = max(1, min(n_bins, 50))
    lo, hi = min(values), max(values)
    if hi == lo:
        hi = lo + 1
    width = (hi - lo) / n_bins
    counts = [0] * n_bins
    for v in values:
        idx = min(int((v - lo) / width), n_bins - 1)
        counts[idx] += 1
    labels = [f"{lo + i * width:.0f}-{lo + (i + 1) * width:.0f}" for i in range(n_bins)]
    return labels, [float(c) for c in counts]


def _svg_open(title: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_W}" height="{_H}" '
        f'viewBox="0 0 {_W} {_H}" font-family="sans-serif">',
        f'<text x="{_W / 2}" y="26" text-anchor="middle" font-size="18" '
        f'font-weight="bold">{escape(title)}</text>',
    ]


def _legend(series: Series, y: float) -> list[str]:
    parts: list[str] = []
    x = float(_LEFT)
    for i, (name, _) in enumerate(series):
        label = name or f"Series {i + 1}"
        parts.append(
            f'<rect x="{x:.0f}" y="{y - 8:.0f}" width="10" height="10" fill="{_color(i)}"/>'
        )
        parts.append(f'<text x="{x + 14:.0f}" y="{y:.0f}" font-size="11">{escape(label)}</text>')
        x += 24 + len(label) * 6.2
    return parts


def _bar_line_svg(spec: ChartSpec) -> str:
    categories, series = spec.categories, spec.series
    n_cat, n_ser = len(categories), len(series)
    legend_h = 18 if n_ser > 1 else 0
    plot_top = _TOP + legend_h
    plot_w = _W - _LEFT - _RIGHT
    plot_h = _H - plot_top - _BOTTOM
    baseline = plot_top + plot_h
    all_v = [v for _, vals in series for v in vals] or [0.0]
    max_v, min_v = max(all_v), min(0.0, min(all_v))
    span = (max_v - min_v) or 1

    def yv(v: float) -> float:
        return baseline - (v - min_v) / span * plot_h

    def val(vals: list[float], ci: int) -> float:
        return vals[ci] if ci < len(vals) else 0.0

    parts = _svg_open(spec.title)
    if n_ser > 1:
        parts += _legend(series, plot_top - 4)
    parts.append(
        f'<line x1="{_LEFT}" y1="{baseline:.1f}" x2="{_W - _RIGHT}" y2="{baseline:.1f}" '
        f'stroke="#888"/>'
    )

    if spec.kind in ("line", "area"):
        step = plot_w / (n_cat - 1) if n_cat > 1 else 0.0
        for si, (_, vals) in enumerate(series):
            xy = [(_LEFT + ci * step, yv(val(vals, ci))) for ci in range(n_cat)]
            if spec.kind == "area":
                area = (
                    f"{xy[0][0]:.1f},{baseline:.1f} "
                    + " ".join(f"{x:.1f},{y:.1f}" for x, y in xy)
                    + f" {xy[-1][0]:.1f},{baseline:.1f}"
                )
                parts.append(
                    f'<polygon points="{area}" fill="{_color(si)}" '
                    f'fill-opacity="0.2" stroke="none"/>'
                )
            pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in xy)
            parts.append(
                f'<polyline points="{pts}" fill="none" stroke="{_color(si)}" stroke-width="2"/>'
            )
            for (cx, cy), ci in zip(xy, range(n_cat), strict=True):
                parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="3" fill="{_color(si)}"/>')
                if n_ser == 1:
                    parts.append(
                        f'<text x="{cx:.1f}" y="{cy - 7:.1f}" text-anchor="middle" '
                        f'font-size="10">{escape(_fmt(val(vals, ci)))}</text>'
                    )
        label_x = [(_LEFT + ci * step) for ci in range(n_cat)]
    elif spec.kind == "histogram":
        slot = plot_w / max(n_cat, 1)
        vals = series[0][1] if series else []
        for ci in range(n_cat):
            v = val(vals, ci)
            x = _LEFT + ci * slot + slot * 0.01
            y = yv(v)
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{slot * 0.98:.1f}" '
                f'height="{baseline - y:.1f}" fill="{_color(0)}"/>'
            )
            if n_cat <= 12:
                parts.append(
                    f'<text x="{x + slot * 0.49:.1f}" y="{y - 4:.1f}" text-anchor="middle" '
                    f'font-size="10">{escape(_fmt(v))}</text>'
                )
        label_x = [(_LEFT + ci * slot + slot / 2) for ci in range(n_cat)]
    else:
        slot = plot_w / max(n_cat, 1)
        group_w = slot * 0.8
        bar_w = group_w / max(n_ser, 1)
        for ci in range(n_cat):
            for si, (_, vals) in enumerate(series):
                v = val(vals, ci)
                x = _LEFT + ci * slot + (slot - group_w) / 2 + si * bar_w
                y = yv(v)
                parts.append(
                    f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" '
                    f'height="{baseline - y:.1f}" fill="{_color(si)}"/>'
                )
                if n_ser == 1:
                    parts.append(
                        f'<text x="{x + bar_w / 2:.1f}" y="{y - 5:.1f}" text-anchor="middle" '
                        f'font-size="11">{escape(_fmt(v))}</text>'
                    )
        label_x = [(_LEFT + ci * slot + slot / 2) for ci in range(n_cat)]

    for ci, cx in enumerate(label_x):
        parts.append(
            f'<text x="{cx:.1f}" y="{baseline + 16:.1f}" text-anchor="middle" '
            f'font-size="11">{escape(categories[ci])}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


def _pie_svg(spec: ChartSpec) -> str:
    labels = spec.categories
    values = spec.series[0][1] if spec.series else []
    total = sum(values) or 1
    cx, cy, r = _W * 0.33, _H * 0.55, 130.0
    parts = _svg_open(spec.title)
    angle = -90.0  # start at 12 o'clock
    for i, value in enumerate(values):
        sweep = value / total * 360
        a1, a2 = math.radians(angle), math.radians(angle + sweep)
        x1, y1 = cx + r * math.cos(a1), cy + r * math.sin(a1)
        x2, y2 = cx + r * math.cos(a2), cy + r * math.sin(a2)
        large = 1 if sweep > 180 else 0
        if sweep >= 359.999:  # single slice = full circle
            parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{_color(i)}"/>')
        else:
            parts.append(
                f'<path d="M{cx:.1f},{cy:.1f} L{x1:.1f},{y1:.1f} '
                f'A{r:.1f},{r:.1f} 0 {large} 1 {x2:.1f},{y2:.1f} Z" fill="{_color(i)}"/>'
            )
        angle += sweep
    ly = 90.0
    for i, (label, value) in enumerate(zip(labels, values, strict=False)):
        pct = value / total * 100
        parts.append(f'<rect x="{_W * 0.62:.0f}" y="{ly - 9:.0f}" width="11" height="11" '
                     f'fill="{_color(i)}"/>')
        parts.append(f'<text x="{_W * 0.62 + 16:.0f}" y="{ly:.0f}" font-size="12">'
                     f'{escape(label)} ({pct:.0f}%)</text>')
        ly += 22
    parts.append("</svg>")
    return "\n".join(parts)


def _scatter_svg(spec: ChartSpec) -> str:
    pts = spec.points
    xs = [p[0] for p in pts] or [0.0]
    ys = [p[1] for p in pts] or [0.0]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    xspan = (xmax - xmin) or 1
    yspan = (ymax - ymin) or 1
    left, top = _LEFT, _TOP
    plot_w = _W - _LEFT - _RIGHT
    plot_h = _H - _TOP - _BOTTOM
    baseline = top + plot_h

    def sx(x: float) -> float:
        return left + (x - xmin) / xspan * plot_w

    def sy(y: float) -> float:
        return baseline - (y - ymin) / yspan * plot_h

    parts = _svg_open(spec.title)
    parts.append(f'<line x1="{left}" y1="{baseline:.1f}" x2="{_W - _RIGHT}" '
                 f'y2="{baseline:.1f}" stroke="#888"/>')
    parts.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{baseline:.1f}" stroke="#888"/>')
    for x, y in pts:
        parts.append(f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="4" fill="{_color(0)}" '
                     f'fill-opacity="0.75"/>')
    if spec.x_label:
        parts.append(f'<text x="{_W / 2:.0f}" y="{_H - 8}" text-anchor="middle" '
                     f'font-size="12">{escape(spec.x_label)}</text>')
    if spec.y_label:
        parts.append(f'<text x="16" y="{_H / 2:.0f}" text-anchor="middle" font-size="12" '
                     f'transform="rotate(-90 16 {_H / 2:.0f})">{escape(spec.y_label)}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def render_chart_svg(spec: ChartSpec) -> str:
    if spec.kind == "pie":
        return _pie_svg(spec)
    if spec.kind == "scatter":
        return _scatter_svg(spec)
    return _bar_line_svg(spec)


def render_bar_svg(title: str, labels: list[str], values: list[float]) -> str:
    return render_chart_svg(ChartSpec("bar", title, labels, [("", values)]))


def render_line_svg(title: str, labels: list[str], values: list[float]) -> str:
    return render_chart_svg(ChartSpec("line", title, labels, [("", values)]))


def render_multi_svg(kind: str, title: str, categories: list[str], series: Series) -> str:
    return render_chart_svg(ChartSpec(kind, title, categories, series))


def _floats(raw: str) -> list[float]:
    out: list[float] = []
    for part in raw.split(","):
        cleaned = part.strip().lstrip("$")
        if not cleaned:
            continue
        try:
            out.append(float(cleaned))
        except ValueError:
            continue
    return out


def _one_float(raw: str) -> float | None:
    try:
        return float(raw.strip().lstrip("$").replace(",", ""))
    except ValueError:
        return None


def parse_chart_block(text: str) -> ChartSpec | None:
    """Parse a ```chart block into a ChartSpec, or None if it has no data.

    Directives (any order): `type: bar|line|pie|scatter`, `title:`, and for category
    charts `x: a, b, c` (multi-series); for scatter `xlabel:`/`ylabel:`. Data lines:
    category charts → `<label>: <number>` (single) or `<Series>: v1, v2, …` (with `x:`);
    scatter → `<x>, <y>` per line.
    """
    kind = "bar"
    title = ""
    x_labels: list[str] | None = None
    x_label = ""
    y_label = ""
    bins = 10
    data: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        low = s.lower()
        if low.startswith("type:"):
            requested = s.split(":", 1)[1].strip().lower()
            if requested in _CHART_KINDS:
                kind = requested
        elif low.startswith("title:"):
            title = s.split(":", 1)[1].strip()
        elif low.startswith("xlabel:"):
            x_label = s.split(":", 1)[1].strip()
        elif low.startswith("ylabel:"):
            y_label = s.split(":", 1)[1].strip()
        elif low.startswith("bins:"):
            parsed = _one_float(s.split(":", 1)[1])
            if parsed:
                bins = int(parsed)
        elif low.startswith("x:"):
            x_labels = [c.strip() for c in s.split(":", 1)[1].split(",") if c.strip()]
        else:
            data.append(s)

    if kind == "scatter":
        points = [(nums[0], nums[1]) for line in data if len(nums := _floats(line)) >= 2]
        return ChartSpec("scatter", title, points=points, x_label=x_label, y_label=y_label) \
            if points else None

    if kind == "histogram":  # bin raw numbers into a frequency distribution
        hnums: list[float] = []
        for line in data:
            hnums += _floats(line.rpartition(":")[2])
        if not hnums:
            return None
        hlabels, hcounts = histogram_bins(hnums, bins)
        return ChartSpec("histogram", title, hlabels, [("", hcounts)])

    if x_labels is not None:  # multi-series
        series: Series = []
        for line in data:
            name, sep, raw = line.rpartition(":")
            if sep and (vals := _floats(raw)):
                series.append((name.strip(), vals))
        return ChartSpec(kind, title, x_labels, series) if series else None

    labels: list[str] = []
    values: list[float] = []
    for line in data:
        name, sep, raw = line.rpartition(":")
        if not sep:
            continue
        v = _one_float(raw)
        if v is not None:
            labels.append(name.strip())
            values.append(v)
    return ChartSpec(kind, title, labels, [(title, values)]) if values else None


class MakeChart:
    name = "make_chart"
    description = (
        "Create a chart, saved as an SVG file. kind = bar (default) / line (trends) / area "
        "(cumulative trend) / pie (shares) / scatter (correlation) / histogram (distribution "
        "of raw numbers). For bar/line/area/pie: `labels` = categories, and either `values` "
        "(one series) or `series` = [{name, values}] (multiple). For scatter: `points` = "
        "[[x, y], …] (+ `xlabel`/`ylabel`). For histogram: `values` = the raw numbers (+ "
        "optional `bins`)."
    )
    dangerous = True  # writes a file
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "path": {"type": "string", "description": "Output .svg file path."},
            "kind": {"type": "string", "enum": list(_CHART_KINDS), "description": "Chart type."},
            "title": {"type": "string", "description": "Chart title."},
            "labels": {"type": "array", "items": {"type": "string"},
                       "description": "x-axis categories (bar/line/pie)."},
            "values": {"type": "array", "items": {"type": "number"},
                       "description": "One value per label (single series)."},
            "series": {"type": "array", "items": {"type": "object"},
                       "description": "Multiple series: each {name, values} aligned to labels."},
            "points": {"type": "array", "items": {"type": "array"},
                       "description": "Scatter [x, y] pairs."},
            "xlabel": {"type": "string", "description": "Scatter x-axis label."},
            "ylabel": {"type": "string", "description": "Scatter y-axis label."},
        },
        "required": ["path"],
    }

    def run(self, args: dict[str, Any]) -> str:
        path = args.get("path")
        title = str(args.get("title", ""))
        if not isinstance(path, str) or not path.strip():
            return "error: 'path' is required"
        kind = args.get("kind", "bar")
        if kind not in _CHART_KINDS:
            kind = "bar"

        spec_or_err = self._build_spec(kind, title, args)
        if isinstance(spec_or_err, str):
            return spec_or_err

        target = Path(path)
        try:
            check_writable(target)
        except PermissionDenied as exc:
            return f"blocked: {exc}"
        try:
            target.expanduser().write_text(render_chart_svg(spec_or_err), encoding="utf-8")
        except OSError as exc:
            return f"error writing {path}: {exc}"
        return f"wrote {kind} chart to {path}"

    def _build_spec(self, kind: str, title: str, args: dict[str, Any]) -> ChartSpec | str:
        if kind == "histogram":
            values = args.get("values")
            if not isinstance(values, list) or not values:
                return "error: histogram needs 'values' = the raw numbers to bin"
            try:
                nums = [float(v) for v in values]
            except (TypeError, ValueError):
                return "error: every value must be a number"
            bins = args.get("bins")
            hlabels, hcounts = histogram_bins(nums, int(bins) if isinstance(bins, int) else 10)
            return ChartSpec("histogram", title, hlabels, [("", hcounts)])

        if kind == "scatter":
            raw = args.get("points")
            if not isinstance(raw, list) or not raw:
                return "error: scatter needs 'points' = [[x, y], …]"
            points: list[tuple[float, float]] = []
            for p in raw:
                try:
                    points.append((float(p[0]), float(p[1])))
                except (TypeError, ValueError, IndexError):
                    return "error: each point must be [x, y] numbers"
            return ChartSpec("scatter", title, points=points,
                             x_label=str(args.get("xlabel", "")),
                             y_label=str(args.get("ylabel", "")))

        labels = args.get("labels")
        if not isinstance(labels, list) or not labels:
            return "error: 'labels' (categories) is required"
        categories = [str(x) for x in labels]
        raw_series = args.get("series")
        if isinstance(raw_series, list) and raw_series:
            series: Series = []
            for item in raw_series:
                if not isinstance(item, dict) or not isinstance(item.get("values"), list):
                    return "error: each series must be {name, values}"
                try:
                    series.append((str(item.get("name", "")), [float(v) for v in item["values"]]))
                except (TypeError, ValueError):
                    return "error: every series value must be a number"
            return ChartSpec(kind, title, categories, series)
        values = args.get("values")
        if not isinstance(values, list) or len(values) != len(labels):
            return "error: pass 'values' (same length as labels) or a 'series' array"
        try:
            return ChartSpec(kind, title, categories, [(title, [float(v) for v in values])])
        except (TypeError, ValueError):
            return "error: every value must be a number"
