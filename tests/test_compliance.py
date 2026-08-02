"""Tests for read_docx + check_requirements (compliance vertical)."""

from typing import Any

from halia.skills.compliance import CheckRequirements
from halia.skills.export import MakeDocx
from halia.skills.word import ReadDocx

_POLICY = """# Data Protection Policy

The company retains personal data for a period of 24 months.
All data is encrypted at rest and in transit.
"""


def _make_policy_docx(tmp_path: Any) -> str:
    out = tmp_path / "policy.docx"
    MakeDocx().run({"path": str(out), "content": _POLICY})
    return str(out)


def test_read_docx_extracts_text(tmp_path: Any) -> None:
    text = ReadDocx().run({"path": _make_policy_docx(tmp_path)})
    assert "Data Protection Policy" in text
    assert "encrypted at rest" in text


def test_read_docx_not_a_file(tmp_path: Any) -> None:
    assert "not a file" in ReadDocx().run({"path": str(tmp_path / "nope.docx")})


def test_read_docx_is_safe() -> None:
    assert ReadDocx().dangerous is False


def test_check_requirements_found_and_missing(tmp_path: Any) -> None:
    path = _make_policy_docx(tmp_path)
    out = CheckRequirements().run(
        {"path": path, "requirements": ["encrypted at rest", "right to erasure"]}
    )
    assert "Coverage: 1/2" in out
    assert "encrypted at rest" in out  # found, with a citation snippet
    assert "…" in out  # snippet delimiters
    assert "MISSING (1)" in out and "right to erasure" in out


def test_check_requirements_reads_the_real_file(tmp_path: Any) -> None:
    # matching is against the document itself, not model-relayed text
    path = _make_policy_docx(tmp_path)
    out = CheckRequirements().run({"path": path, "requirements": ["24 months"]})
    assert "Coverage: 1/1" in out


def test_check_requirements_validates(tmp_path: Any) -> None:
    path = _make_policy_docx(tmp_path)
    assert "requirements" in CheckRequirements().run({"path": path, "requirements": []})
    assert "path" in CheckRequirements().run({"path": "", "requirements": ["x"]})


def test_check_requirements_is_safe() -> None:
    assert CheckRequirements().dangerous is False


def test_compliance_preset_wired() -> None:
    from halia.presets import get_preset, preset_names
    from halia.skills import available_skills

    assert "read_docx" in available_skills()
    assert "check_requirements" in available_skills()
    cp = get_preset("compliance")
    assert cp is not None
    assert {"check_requirements", "read_docx"} <= set(cp.skills)
    assert "compliance" in preset_names()
