"""Number grounding — the conscience's teeth.

Every monetary/decimal figure in the answer should trace to a tool result. This
scans the drafted answer for figures and flags any that don't appear in the
run's tool outputs — catching numbers the model computed *in its head* (the
invented total) rather than via calculate/aggregate/reconcile.

Deterministic and cheap. Stage 1 = FLAG (surface + audit). Stage 2 (later) feeds
the flags back for the model to recompute.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from halia.audit.trace import Step

# Strip ISO dates first so their digits aren't treated as figures.
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
# A number not glued to a letter/digit/dot (so ids like T1 and versions 1.2.3 don't match).
_NUM = re.compile(r"(?<![A-Za-z0-9.])\$?-?\d[\d,]*(?:\.\d+)?")


def _extract(text: str, figures_only: bool) -> set[Decimal]:
    cleaned = _DATE.sub(" ", text)
    found: set[Decimal] = set()
    for match in _NUM.finditer(cleaned):
        token = match.group()
        # A "figure" to verify has a decimal point, currency sign, or thousands comma;
        # bare integers (counts, years, ids) are too noisy to check in the answer.
        if figures_only and not any(mark in token for mark in ".$,"):
            continue
        normalized = token.lstrip("$").replace(",", "").strip()
        try:
            found.add(Decimal(normalized))
        except InvalidOperation:
            continue
    return found


def ungrounded_numbers(answer: str, steps: list[Step]) -> list[str]:
    """Figures in `answer` that don't appear in any tool observation."""
    grounded: set[Decimal] = set()
    for step in steps:
        grounded |= _extract(step.observation, figures_only=False)
    answer_figures = _extract(answer, figures_only=True)
    return [format(number, "f") for number in sorted(answer_figures - grounded)]
