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

_W, _H = 640, 400
_LEFT, _RIGHT, _TOP, _BOTTOM = 60, 20, 48, 70


def render_bar_svg(title: str, labels: list[str], values: list[float]) -> str:
    """Render a bar chart as an SVG string (pure — no I/O)."""
    plot_w = _W - _LEFT - _RIGHT
    plot_h = _H - _TOP - _BOTTOM
    max_v = max(values)
    n = len(values)
    slot = plot_w / n
    bar_w = slot * 0.6

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_W}" height="{_H}" '
        f'viewBox="0 0 {_W} {_H}" font-family="sans-serif">',
        f'<text x="{_W / 2}" y="26" text-anchor="middle" font-size="18" '
        f'font-weight="bold">{escape(title)}</text>',
        f'<line x1="{_LEFT}" y1="{_TOP + plot_h}" x2="{_W - _RIGHT}" '
        f'y2="{_TOP + plot_h}" stroke="#888"/>',
    ]
    for i, (label, value) in enumerate(zip(labels, values, strict=True)):
        bar_h = (value / max_v) * plot_h if max_v > 0 else 0
        x = _LEFT + i * slot + (slot - bar_w) / 2
        y = _TOP + plot_h - bar_h
        cx = x + bar_w / 2
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" '
            f'fill="#4f7cff"/>'
        )
        parts.append(
            f'<text x="{cx:.1f}" y="{y - 5:.1f}" text-anchor="middle" font-size="11">'
            f"{escape(_fmt(value))}</text>"
        )
        parts.append(
            f'<text x="{cx:.1f}" y="{_TOP + plot_h + 16:.1f}" text-anchor="middle" '
            f'font-size="11">{escape(label)}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}"


def parse_chart_block(text: str) -> tuple[str, list[str], list[float]] | None:
    """Parse a ```chart block body into (title, labels, values), or None if empty.

    Format (one per line): `title: <title>` (optional), then `<label>: <number>`.
    Shared by the PDF and PPTX renderers so a chart in the markdown master renders
    natively in each format (no SVG→raster).
    """
    title = ""
    labels: list[str] = []
    values: list[float] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.lower().startswith("title:"):
            title = line.split(":", 1)[1].strip()
            continue
        label, sep, raw = line.rpartition(":")
        if not sep:
            continue
        try:
            values.append(float(raw.strip().lstrip("$").replace(",", "")))
        except ValueError:
            continue
        labels.append(label.strip())
    if not values:
        return None
    return (title, labels, values)


class MakeChart:
    name = "make_chart"
    description = (
        "Create a bar chart from labels and values, saved as an SVG file. Use for "
        "performance tables, grade distributions, attendance, and similar summaries."
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
                "description": "One label per bar (x-axis).",
            },
            "values": {
                "type": "array",
                "items": {"type": "number"},
                "description": "One numeric value per bar, same length as labels.",
            },
            "title": {"type": "string", "description": "Chart title."},
        },
        "required": ["path", "labels", "values"],
    }

    def run(self, args: dict[str, Any]) -> str:
        path = args.get("path")
        labels = args.get("labels")
        values = args.get("values")
        title = args.get("title", "")
        if not isinstance(path, str) or not path.strip():
            return "error: 'path' is required"
        if not isinstance(labels, list) or not labels:
            return "error: 'labels' must be a non-empty array"
        if not isinstance(values, list) or len(values) != len(labels):
            return "error: 'values' must be an array the same length as 'labels'"
        try:
            nums = [float(v) for v in values]
        except (TypeError, ValueError):
            return "error: every value must be a number"

        target = Path(path)
        try:
            check_writable(target)
        except PermissionDenied as exc:
            return f"blocked: {exc}"

        svg = render_bar_svg(str(title), [str(x) for x in labels], nums)
        try:
            target.expanduser().write_text(svg, encoding="utf-8")
        except OSError as exc:
            return f"error writing {path}: {exc}"
        return f"wrote bar chart ({len(nums)} bars) to {path}"
