"""Chart skill — turn data into a simple bar chart, as an SVG file.

The "build graph" capability for clerical work (pupil performance, attendance,
grade distributions…). SVG is plain text, so this needs NO plotting dependency
(no matplotlib) and the output renders in any browser — staying lean.

Writes a file, so — like write_file — it is dangerous (approval-gated) and passes
through the filesystem permission floor.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from halia.permissions.guard import PermissionDenied, check_writable

_W, _H = 680, 420
_LEFT, _RIGHT, _TOP, _BOTTOM = 60, 20, 48, 70
_PALETTE = ["#4f7cff", "#ff6b6b", "#2ecc71", "#f1c40f", "#9b59b6", "#e67e22"]

_CHART_KINDS = ("bar", "line")

# A named series of values aligned to the chart's categories.
Series = list[tuple[str, list[float]]]


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}"


def _color(i: int) -> str:
    return _PALETTE[i % len(_PALETTE)]


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


def render_multi_svg(kind: str, title: str, categories: list[str], series: Series) -> str:
    """Render a bar (grouped) or line (multi) chart as SVG — one or several named series."""
    n_cat = len(categories)
    n_ser = len(series)
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

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_W}" height="{_H}" '
        f'viewBox="0 0 {_W} {_H}" font-family="sans-serif">',
        f'<text x="{_W / 2}" y="26" text-anchor="middle" font-size="18" '
        f'font-weight="bold">{escape(title)}</text>',
    ]
    if n_ser > 1:
        parts += _legend(series, plot_top - 4)
    parts.append(
        f'<line x1="{_LEFT}" y1="{baseline:.1f}" x2="{_W - _RIGHT}" y2="{baseline:.1f}" '
        f'stroke="#888"/>'
    )

    if kind == "line":
        step = plot_w / (n_cat - 1) if n_cat > 1 else 0.0
        for si, (_, vals) in enumerate(series):
            pts = " ".join(
                f"{_LEFT + ci * step:.1f},{yv(val(vals, ci)):.1f}" for ci in range(n_cat)
            )
            parts.append(
                f'<polyline points="{pts}" fill="none" stroke="{_color(si)}" stroke-width="2"/>'
            )
            for ci in range(n_cat):
                cx, cy = _LEFT + ci * step, yv(val(vals, ci))
                parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="3" fill="{_color(si)}"/>')
                if n_ser == 1:
                    parts.append(
                        f'<text x="{cx:.1f}" y="{cy - 7:.1f}" text-anchor="middle" '
                        f'font-size="10">{escape(_fmt(val(vals, ci)))}</text>'
                    )
        label_x = [(_LEFT + ci * step) for ci in range(n_cat)]
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


def render_bar_svg(title: str, labels: list[str], values: list[float]) -> str:
    """Single-series bar chart (convenience wrapper)."""
    return render_multi_svg("bar", title, labels, [("", values)])


def render_line_svg(title: str, labels: list[str], values: list[float]) -> str:
    """Single-series line chart (convenience wrapper)."""
    return render_multi_svg("line", title, labels, [("", values)])


def render_chart_svg(kind: str, title: str, categories: list[str], series: Series) -> str:
    return render_multi_svg(kind, title, categories, series)


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


def parse_chart_block(text: str) -> tuple[str, str, list[str], Series] | None:
    """Parse a ```chart block into (kind, title, categories, series), or None if empty.

    Single-series: `type:`/`title:` (optional) then `<label>: <number>` lines.
    Multi-series: add an `x: a, b, c` line (categories), then one `<Series>: v1, v2, v3`
    line per series (comma-separated, no thousands commas). Shared by the PDF/PPTX/DOCX
    renderers so a chart in the markdown master renders natively in each format.
    """
    kind = "bar"
    title = ""
    x_labels: list[str] | None = None
    entries: list[tuple[str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("type:"):
            requested = line.split(":", 1)[1].strip().lower()
            if requested in _CHART_KINDS:
                kind = requested
            continue
        if low.startswith("title:"):
            title = line.split(":", 1)[1].strip()
            continue
        if low.startswith("x:"):
            x_labels = [c.strip() for c in line.split(":", 1)[1].split(",") if c.strip()]
            continue
        name, sep, raw = line.rpartition(":")
        if sep:
            entries.append((name.strip(), raw.strip()))

    if x_labels is not None:  # multi-series
        series: Series = [(name, _floats(raw)) for name, raw in entries]
        series = [(n, v) for n, v in series if v]
        return (kind, title, x_labels, series) if series else None

    labels: list[str] = []
    values: list[float] = []
    for name, raw in entries:
        v = _one_float(raw)
        if v is not None:
            labels.append(name)
            values.append(v)
    if not values:
        return None
    return (kind, title, labels, [(title, values)])


class MakeChart:
    name = "make_chart"
    description = (
        "Create a bar or line chart, saved as an SVG file. `labels` are the x-axis "
        "categories. For ONE series pass `values`; for MULTIPLE series (grouped bars / "
        "multi-line) pass `series` — a list of {name, values} each aligned to labels. Use "
        "line for trends over time, bar for comparisons (default)."
    )
    dangerous = True  # writes a file
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "path": {"type": "string", "description": "Output .svg file path."},
            "labels": {
                "type": "array",
                "items": {"type": "string"},
                "description": "The x-axis categories.",
            },
            "values": {
                "type": "array",
                "items": {"type": "number"},
                "description": "One value per label (single series).",
            },
            "series": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Multiple series: each {name, values} aligned to labels.",
            },
            "title": {"type": "string", "description": "Chart title."},
            "kind": {
                "type": "string",
                "enum": list(_CHART_KINDS),
                "description": "bar (default) or line.",
            },
        },
        "required": ["path", "labels"],
    }

    def run(self, args: dict[str, Any]) -> str:
        path = args.get("path")
        labels = args.get("labels")
        title = str(args.get("title", ""))
        if not isinstance(path, str) or not path.strip():
            return "error: 'path' is required"
        if not isinstance(labels, list) or not labels:
            return "error: 'labels' must be a non-empty array"
        categories = [str(x) for x in labels]

        series: Series = []
        raw_series = args.get("series")
        if isinstance(raw_series, list) and raw_series:
            for item in raw_series:
                if not isinstance(item, dict) or not isinstance(item.get("values"), list):
                    return "error: each series must be an object with a 'values' array"
                try:
                    vals = [float(v) for v in item["values"]]
                except (TypeError, ValueError):
                    return "error: every series value must be a number"
                series.append((str(item.get("name", "")), vals))
        else:
            values = args.get("values")
            if not isinstance(values, list) or len(values) != len(labels):
                return "error: pass 'values' (same length as labels) or a 'series' array"
            try:
                series = [(title, [float(v) for v in values])]
            except (TypeError, ValueError):
                return "error: every value must be a number"

        target = Path(path)
        try:
            check_writable(target)
        except PermissionDenied as exc:
            return f"blocked: {exc}"

        kind = args.get("kind", "bar")
        if kind not in _CHART_KINDS:
            kind = "bar"
        svg = render_chart_svg(kind, title, categories, series)
        try:
            target.expanduser().write_text(svg, encoding="utf-8")
        except OSError as exc:
            return f"error writing {path}: {exc}"
        return f"wrote {kind} chart ({len(series)} series × {len(categories)} categories) to {path}"
