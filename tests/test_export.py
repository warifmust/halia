"""Tests for make_pdf (markdown -> PDF render)."""

from typing import Any

from halia.skills.export import MakePdf, render_markdown_pdf
from halia.skills.pdf import ReadPdf

_MD = """# Class Report

A short **summary** of the term.

## Scores

| Student | Average |
| --- | --- |
| Aisha | 78.33 |
| Chong | 90.00 |

## Notes
- Chong is top of the class.
- Dan needs support.

1. Review results.
2. Send letters home.
"""


def test_render_returns_pdf_bytes(tmp_path: Any) -> None:
    out = tmp_path / "r.pdf"
    render_markdown_pdf(_MD).output(str(out))
    assert out.read_bytes().startswith(b"%PDF")


def test_pdf_roundtrips_back_to_text(tmp_path: Any) -> None:
    out = tmp_path / "r.pdf"
    render_markdown_pdf(_MD).output(str(out))
    # our own read_pdf should recover the content
    text = ReadPdf().run({"path": str(out)})
    assert "Class Report" in text
    assert "Chong" in text and "90.00" in text
    assert "Send letters home" in text


def test_make_pdf_writes_pdf_and_markdown_source(tmp_path: Any) -> None:
    out = tmp_path / "report.pdf"
    result = MakePdf().run({"path": str(out), "content": _MD})
    assert "wrote PDF" in result
    assert out.read_bytes().startswith(b"%PDF")
    # the editable markdown master is kept alongside
    md = tmp_path / "report.md"
    assert md.exists() and md.read_text() == _MD


def test_make_pdf_title_is_prepended(tmp_path: Any) -> None:
    out = tmp_path / "t.pdf"
    MakePdf().run({"path": str(out), "content": "Body text.", "title": "My Title"})
    assert (tmp_path / "t.md").read_text().startswith("# My Title")


def test_make_pdf_renders_unicode(tmp_path: Any) -> None:
    from halia.skills.pdf import ReadPdf

    out = tmp_path / "u.pdf"
    r = MakePdf().run({"path": str(out), "content": "# Café\n\nZürich — naïve … Привет"})
    assert "wrote PDF" in r
    text = ReadPdf().run({"path": str(out)})
    # bundled DejaVu font preserves the characters instead of sanitising to '?'
    assert "Café" in text and "Zürich" in text and "Привет" in text


def test_make_pdf_validates(tmp_path: Any) -> None:
    assert "content" in MakePdf().run({"path": str(tmp_path / "x.pdf"), "content": "  "})
    assert "path" in MakePdf().run({"path": "", "content": "hi"})


def test_make_pdf_honors_permission_floor(tmp_path: Any) -> None:
    blocked = tmp_path / "id_rsa.pdf"
    r = MakePdf().run({"path": str(blocked), "content": "secret"})
    assert r.startswith("blocked:")
    assert not blocked.exists()


def test_pdf_embeds_a_chart_block(tmp_path: Any) -> None:
    md = "# Report\n\nIntro.\n\n```chart\ntitle: Scores\nA: 10\nB: 20\n```\n\nAfter."
    out = tmp_path / "c.pdf"
    render_markdown_pdf(md).output(str(out))  # native bars drawn — must not crash
    assert out.read_bytes().startswith(b"%PDF")


def test_make_pdf_is_dangerous() -> None:
    assert MakePdf().dangerous is True


def test_make_pdf_wired_into_catalogue_and_presets() -> None:
    from halia.presets import get_preset
    from halia.skills import available_skills, build_registry

    assert "make_pdf" in available_skills()
    assert build_registry(["make_pdf"]).get("make_pdf") is not None
    for vertical in ("finance", "research", "education"):
        assert "make_pdf" in get_preset(vertical).skills
