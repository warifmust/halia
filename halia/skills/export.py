"""Export skills — render halia's markdown content to printable artifacts.

Per the decided model: **markdown is the master**, and each output format is a direct
*render* from it (never a format→format conversion — that would need LibreOffice).
`make_pdf` is the first render. It keeps the markdown source alongside the PDF, so the
PDF stays a disposable print product and the markdown remains the editable working copy.

Renders a pragmatic markdown subset (headings, paragraphs, bold, bullet/numbered lists,
simple tables, rules) with fpdf2 — lean, no browser, no LibreOffice.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fpdf import FPDF

from halia.permissions.guard import PermissionDenied, check_writable

_HEADING_SIZES = {1: 20, 2: 16, 3: 14, 4: 12, 5: 11, 6: 11}
# Core PDF fonts are latin-1 only; map common typographic characters so content
# doesn't crash the renderer. (Full-Unicode output needs a bundled TTF — deferred.)
_REPLACEMENTS = {
    "—": "-", "–": "-", "‘": "'", "’": "'",
    "“": '"', "”": '"', "…": "...", "•": "-", " ": " ",
}


def _s(text: str) -> str:
    for uni, ascii_ in _REPLACEMENTS.items():
        text = text.replace(uni, ascii_)
    return text.encode("latin-1", "replace").decode("latin-1")


def _truncate(pdf: FPDF, text: str, max_w: float) -> str:
    if pdf.get_string_width(text) <= max_w:
        return text
    while text and pdf.get_string_width(text + "...") > max_w:
        text = text[:-1]
    return text + "..."


def _render_table(pdf: FPDF, block: list[str]) -> None:
    rows: list[list[str]] = []
    for raw in block:
        if re.match(r"^\|[\s:|-]+\|$", raw):  # the |---|---| separator row
            continue
        rows.append([c.strip() for c in raw.strip().strip("|").split("|")])
    if not rows:
        return
    ncols = max(len(r) for r in rows)
    col_w = (pdf.w - pdf.l_margin - pdf.r_margin) / ncols
    for i, row in enumerate(rows):
        cells = row + [""] * (ncols - len(row))
        pdf.set_font("Helvetica", "B" if i == 0 else "", 10)
        for cell in cells:
            pdf.cell(col_w, 8, _truncate(pdf, _s(cell), col_w - 2), border=1)
        pdf.ln()
    pdf.ln(2)


def render_markdown_pdf(content: str) -> FPDF:
    """Render a markdown subset to a clean FPDF document (pure — caller does the I/O)."""
    pdf = FPDF()
    pdf.set_margins(20, 20, 20)
    pdf.set_auto_page_break(True, margin=18)
    pdf.add_page()

    lines = content.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()

        if stripped.startswith("|") and stripped.endswith("|"):
            block: list[str] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i].strip())
                i += 1
            _render_table(pdf, block)
            continue
        i += 1

        if not stripped:
            pdf.ln(3)
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)", stripped)
        if heading:
            level = len(heading.group(1))
            pdf.ln(2)
            pdf.set_font("Helvetica", "B", _HEADING_SIZES.get(level, 12))
            pdf.multi_cell(0, _HEADING_SIZES.get(level, 12) * 0.5 + 2, _s(heading.group(2)))
            pdf.ln(1)
            continue

        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", stripped):
            pdf.ln(1)
            y = pdf.get_y()
            pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
            pdf.ln(3)
            continue

        bullet = re.match(r"^[-*]\s+(.*)", stripped)
        numbered = re.match(r"^(\d+)\.\s+(.*)", stripped)
        pdf.set_font("Helvetica", "", 11)
        if bullet:
            pdf.set_x(pdf.l_margin + 6)
            pdf.multi_cell(0, 6, _s("- " + bullet.group(1)), markdown=True)
        elif numbered:
            pdf.set_x(pdf.l_margin + 6)
            pdf.multi_cell(0, 6, _s(f"{numbered.group(1)}. {numbered.group(2)}"), markdown=True)
        else:
            pdf.multi_cell(0, 6, _s(stripped), markdown=True)
            pdf.ln(1)
    return pdf


class MakePdf:
    name = "make_pdf"
    description = (
        "Render markdown/text content to a clean, printable PDF (headings, bold, bullet "
        "and numbered lists, simple tables). The editable markdown source is saved "
        "alongside the PDF — edit that and re-render rather than editing the PDF."
    )
    dangerous = True  # writes files
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "path": {"type": "string", "description": "Output .pdf file path."},
            "content": {"type": "string", "description": "The markdown/text content."},
            "title": {"type": "string", "description": "Optional title (rendered as a heading)."},
        },
        "required": ["path", "content"],
    }

    def run(self, args: dict[str, Any]) -> str:
        path = args.get("path")
        content = args.get("content")
        title = args.get("title")
        if not isinstance(path, str) or not path.strip():
            return "error: 'path' is required"
        if not isinstance(content, str) or not content.strip():
            return "error: 'content' is required and must be non-empty"
        if isinstance(title, str) and title.strip():
            content = f"# {title.strip()}\n\n{content}"

        pdf_path = Path(path).expanduser()
        md_path = pdf_path.with_suffix(".md")  # the editable master, kept alongside
        try:
            check_writable(pdf_path)
            check_writable(md_path)
        except PermissionDenied as exc:
            return f"blocked: {exc}"

        try:
            render_markdown_pdf(content).output(str(pdf_path))
            md_path.write_text(content, encoding="utf-8")
        except OSError as exc:
            return f"error writing {path}: {exc}"
        return f"wrote PDF to {pdf_path} (editable markdown source kept at {md_path})"
