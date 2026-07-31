"""Tests for the read_pdf skill (PDF generated with fpdf2)."""

from typing import Any

from fpdf import FPDF

from halia.skills.pdf import ReadPdf


def _make_pdf(tmp_path: Any, text: str = "Invoice total is 1234.56 dollars.") -> Any:
    path = tmp_path / "doc.pdf"
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", size=12)
    pdf.multi_cell(0, 10, text)
    pdf.output(str(path))
    return path


def test_read_pdf_extracts_text(tmp_path: Any) -> None:
    out = ReadPdf().run({"path": str(_make_pdf(tmp_path))})
    assert "1234.56" in out
    assert "1 page(s)" in out


def test_read_pdf_requires_path() -> None:
    assert "required" in ReadPdf().run({})


def test_read_pdf_not_a_file(tmp_path: Any) -> None:
    assert "not a file" in ReadPdf().run({"path": str(tmp_path / "nope.pdf")})


def test_read_pdf_is_safe() -> None:
    assert ReadPdf().dangerous is False
