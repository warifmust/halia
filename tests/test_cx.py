"""Tests for the customer-experience (cx) preset."""

from __future__ import annotations

from halia.presets import get_preset, preset_names


def test_cx_preset_wired() -> None:
    cp = get_preset("cx")
    assert cp is not None
    assert "cx" in preset_names()
    # the metric/analysis + instrument-design + delivery skills it needs
    assert {"query_data", "group_by", "aggregate_csv"} <= set(cp.skills)  # compute NPS/CSAT
    assert {"make_diagram", "make_chart"} <= set(cp.skills)  # journey map + distributions
    assert {"readability", "count_text"} <= set(cp.skills)  # instrument wording
    assert {"make_pptx", "make_docx", "make_excel"} <= set(cp.skills)  # deliverables


def test_cx_prompt_bakes_in_trust_discipline() -> None:
    prompt = get_preset("cx").extra_prompt  # type: ignore[union-attr]
    low = prompt.lower()
    assert "nps" in low  # names the core metric
    assert "verbatim" in low and "invent" in low  # sentiment grounded in real quotes
    assert "do not conduct" in low or "not conduct the fieldwork" in low  # honest boundary
    # generalized — no brand/domain leaks
    assert not any(t in low for t in ("setel", "ekyc", "zendesk"))
