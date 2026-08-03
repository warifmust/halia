"""Tests for check_qa_artifact (QA-artifact completeness hook)."""

from halia.skills.qa import CheckQaArtifact

_GOOD_BUG = """Title: Login button unresponsive
Steps to reproduce:
1. Go to /login
2. Click Login
Expected result: redirected to dashboard.
Actual result: nothing happens.
Environment: Chrome 120, macOS 14
"""

_GOOD_CASE = """Test case: valid login
Preconditions: a registered account exists.
Steps:
1. Open /login
2. Submit valid credentials
Expected result: dashboard loads.
"""


def test_complete_bug_report() -> None:
    out = CheckQaArtifact().run({"text": _GOOD_BUG, "kind": "bug_report"})
    assert "4/4 required fields present" in out
    assert "MISSING: none" in out


def test_incomplete_bug_report_flags_missing() -> None:
    text = "The login button doesn't work when I click it."
    out = CheckQaArtifact().run({"text": text, "kind": "bug_report"})
    assert "0/4" in out
    for field in ("steps to reproduce", "expected result", "actual result", "environment"):
        assert field in out


def test_variant_matching() -> None:
    # abbreviated field labels still count as present
    text = "Repro: click it.\nExpected: works.\nActual: broken.\nBrowser: Firefox."
    out = CheckQaArtifact().run({"text": text, "kind": "bug_report"})
    assert "4/4" in out  # repro/expected/actual/browser all matched


def test_complete_test_case() -> None:
    out = CheckQaArtifact().run({"text": _GOOD_CASE, "kind": "test_case"})
    assert "3/3 required fields present" in out


def test_test_case_missing_preconditions() -> None:
    text = "Steps: open the app.\nExpected result: it opens."
    out = CheckQaArtifact().run({"text": text, "kind": "test_case"})
    assert "2/3" in out
    assert "MISSING (1)" in out and "preconditions" in out


def test_validation() -> None:
    assert "text" in CheckQaArtifact().run({"text": "  ", "kind": "bug_report"})
    assert "kind" in CheckQaArtifact().run({"text": "x", "kind": "nope"})


def test_safe_and_wired() -> None:
    from halia.presets import get_preset, preset_names
    from halia.skills import available_skills, default_registry

    assert CheckQaArtifact().dangerous is False
    assert "check_qa_artifact" in available_skills()
    assert default_registry().get("check_qa_artifact") is not None  # auto-joined default
    qa = get_preset("qa")
    assert qa is not None and "check_qa_artifact" in qa.skills
    assert "qa" in preset_names()
