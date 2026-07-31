"""Tests for the deterministic calculate skill + its safe evaluator."""

import pytest

from halia.skills.calc import CalcError, Calculate, safe_eval


def test_arithmetic() -> None:
    assert safe_eval("2 + 3 * 4") == 14
    assert safe_eval("(2 + 3) * 4") == 20
    assert safe_eval("10 / 4") == 2.5
    assert safe_eval("2 ** 10") == 1024
    assert safe_eval("17 % 5") == 2
    assert safe_eval("-7 + 2") == -5


def test_skill_formats_int_and_float() -> None:
    assert Calculate().run({"expression": "6 * 7"}) == "42"
    assert Calculate().run({"expression": "1 / 8"}) == "0.125"


def test_skill_requires_expression() -> None:
    assert "required" in Calculate().run({})


def test_division_by_zero_is_handled() -> None:
    out = Calculate().run({"expression": "1 / 0"})
    assert "error" in out


def test_no_code_execution() -> None:
    # Names / calls / attributes are not whitelisted nodes → rejected, never run.
    with pytest.raises(CalcError):
        safe_eval("__import__('os').system('echo hacked')")
    assert "error" in Calculate().run({"expression": "os.system('x')"})
