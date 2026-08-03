"""Tests for check_expectation (deterministic PASS/FAIL verdict)."""

from halia.skills.expectation import CheckExpectation


def _run(**args: str) -> str:
    return CheckExpectation().run(dict(args))


def test_equals_pass_and_fail() -> None:
    assert _run(actual="200", operator="equals", expected="200").startswith("PASS")
    assert _run(actual="404", operator="equals", expected="200").startswith("FAIL")


def test_equals_trims_whitespace() -> None:
    assert _run(actual=" 200 ", operator="equals", expected="200").startswith("PASS")


def test_not_equals() -> None:
    assert _run(actual="404", operator="not_equals", expected="200").startswith("PASS")
    assert _run(actual="200", operator="not_equals", expected="200").startswith("FAIL")


def test_contains_and_not_contains() -> None:
    assert _run(actual="user not found", operator="contains", expected="not found").startswith(
        "PASS"
    )
    assert _run(actual="ok", operator="not_contains", expected="error").startswith("PASS")


def test_numeric_operators_use_exact_decimal() -> None:
    assert _run(actual="19.99", operator="greater_than", expected="19.9").startswith("PASS")
    assert _run(actual="5", operator="at_least", expected="5").startswith("PASS")
    assert _run(actual="4", operator="at_least", expected="5").startswith("FAIL")
    assert _run(actual="3", operator="less_than", expected="3").startswith("FAIL")
    assert _run(actual="3", operator="at_most", expected="3").startswith("PASS")


def test_numeric_on_non_number_is_error_not_verdict() -> None:
    out = _run(actual="fast", operator="greater_than", expected="10")
    assert out.startswith("error")  # not a false PASS/FAIL


def test_matches_regex() -> None:
    iso = _run(actual="2026-08-03", operator="matches", expected=r"^\d{4}-\d{2}-\d{2}$")
    assert iso.startswith("PASS")
    assert _run(actual="nope", operator="matches", expected=r"^\d+$").startswith("FAIL")


def test_invalid_regex_is_error() -> None:
    assert _run(actual="x", operator="matches", expected="(unclosed").startswith("error")


def test_label_is_echoed() -> None:
    out = _run(actual="200", operator="equals", expected="200", label="TC-1")
    assert "[TC-1]" in out


def test_output_quotes_both_sides_for_audit() -> None:
    out = _run(actual="404", operator="equals", expected="200")
    assert '"404"' in out and '"200"' in out


def test_validation() -> None:
    assert "operator" in _run(actual="1", operator="nope", expected="1")


def test_safe_and_wired() -> None:
    from halia.presets import get_preset
    from halia.skills import DEFAULT_SKILLS, available_skills, default_registry

    assert CheckExpectation().dangerous is False
    assert "check_expectation" in available_skills()
    assert "check_expectation" in DEFAULT_SKILLS  # auto-joins the generalist
    assert default_registry().get("check_expectation") is not None
    qa = get_preset("qa")
    assert qa is not None and "check_expectation" in qa.skills
