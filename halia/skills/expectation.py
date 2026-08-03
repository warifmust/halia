"""check_expectation — a deterministic PASS/FAIL verdict on one comparison.

The trust capstone for testing: a test's verdict must not be the model's impression
("looks like a pass") — it must be a real, deterministic comparison that lands in the
audit trail. This skill takes an actual value, an operator, and an expected value, and
returns PASS or FAIL, quoting both sides so the judgment is auditable. Numeric
comparisons use exact `Decimal` (consistent with halia's money-exact discipline).

Read-only and side-effect-free (`dangerous=False`). Generally useful beyond QA — any
place a claim needs an exact, grounded check rather than a guess.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

# operator -> (affirmative phrase, is-numeric)
_OPERATORS: dict[str, tuple[str, bool]] = {
    "equals": ("equals", False),
    "not_equals": ("does not equal", False),
    "contains": ("contains", False),
    "not_contains": ("does not contain", False),
    "greater_than": ("is greater than", True),
    "less_than": ("is less than", True),
    "at_least": ("is at least", True),
    "at_most": ("is at most", True),
    "matches": ("matches pattern", False),
}


def _evaluate(actual: str, operator: str, expected: str) -> tuple[bool, str]:
    """Return (passed, detail-or-error). detail is '' unless there's an error."""
    a, e = actual.strip(), expected.strip()

    if operator in ("greater_than", "less_than", "at_least", "at_most"):
        try:
            na, ne = Decimal(a), Decimal(e)
        except InvalidOperation:
            return False, f"cannot compare non-numeric values ('{a}', '{e}') numerically"
        if operator == "greater_than":
            return na > ne, ""
        if operator == "less_than":
            return na < ne, ""
        if operator == "at_least":
            return na >= ne, ""
        return na <= ne, ""  # at_most

    if operator == "equals":
        return a == e, ""
    if operator == "not_equals":
        return a != e, ""
    if operator == "contains":
        return e in a, ""
    if operator == "not_contains":
        return e not in a, ""
    if operator == "matches":
        try:
            return re.search(e, a) is not None, ""
        except re.error as exc:
            return False, f"invalid regular expression '{e}': {exc}"
    return False, f"unknown operator '{operator}'"  # unreachable (schema-guarded)


class CheckExpectation:
    name = "check_expectation"
    description = (
        "Deterministically check one actual value against an expected value and return "
        "PASS or FAIL. Use this to decide a test's verdict (e.g. actual HTTP status equals "
        "expected) instead of judging by eye — the comparison is exact and lands in the "
        "audit trail. Operators: equals, not_equals, contains, not_contains, greater_than, "
        "less_than, at_least, at_most, matches (regex). Numeric operators use exact decimals."
    )
    dangerous = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "actual": {"type": "string", "description": "The observed value (e.g. '404')."},
            "operator": {
                "type": "string",
                "enum": list(_OPERATORS),
                "description": "How to compare actual against expected.",
            },
            "expected": {
                "type": "string",
                "description": "The expected value (for 'matches', a regular expression).",
            },
            "label": {
                "type": "string",
                "description": "Optional label for this check (e.g. a test id).",
            },
        },
        "required": ["actual", "operator", "expected"],
    }

    def run(self, args: dict[str, Any]) -> str:
        actual = args.get("actual")
        expected = args.get("expected")
        operator = args.get("operator")
        if not isinstance(actual, str) or not isinstance(expected, str):
            return "error: 'actual' and 'expected' are required strings"
        if operator not in _OPERATORS:
            return f"error: operator must be one of {', '.join(_OPERATORS)}"
        label = args.get("label")
        tag = f"[{label}] " if isinstance(label, str) and label.strip() else ""

        passed, error = _evaluate(actual, operator, expected)
        if error:
            return f"error: {tag}{error}"
        phrase, _ = _OPERATORS[operator]
        verdict = "PASS" if passed else "FAIL"
        negate = "" if passed else "NOT "
        a, e = actual.strip(), expected.strip()
        return f'{verdict}: {tag}actual "{a}" {negate}{phrase} expected "{e}"'
