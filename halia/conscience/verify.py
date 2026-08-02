"""Number grounding — the conscience's teeth.

Every monetary/decimal figure in the answer should trace to a tool result. This
scans the drafted answer for figures and flags any that don't appear in the
run's tool outputs — catching numbers the model computed *in its head* (the
invented total) rather than via calculate/aggregate/reconcile.

Deterministic and cheap. Stage 1 = FLAG (surface + audit). Stage 2, in the agent
loop, feeds these flags back so the model recomputes the offending figures through
tools before finalizing (see `halia.core.agent.run`).
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

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


def _is_grounded(figure: Decimal, tool_figures: set[Decimal]) -> bool:
    """A figure is grounded if its magnitude equals a tool figure's OR is a correct rounding.

    Matching is **sign-insensitive**: a bank statement expresses a debit as `-1,250.00`,
    but prose reports the magnitude `$1,250.00` — the same fact. So `181.38` passes
    against a tool's `181.375` (correct 2-dp rounding) and `1250.00` passes against a
    tool's `-1250.00`; a *mis*-rounding like `181.40` or an invented number still fails.
    (Trade-off: a genuine sign-flip in prose won't be caught — acceptable, since the
    debit/credit convention mismatch is far more common than a flipped sign.)
    """
    target = abs(figure)
    tool_mags = {abs(t) for t in tool_figures}
    if target in tool_mags:
        return True
    exponent = figure.as_tuple().exponent
    if not isinstance(exponent, int):  # nan/inf — never grounded
        return False
    quantum = Decimal(1).scaleb(-max(0, -exponent))  # figure's decimal precision
    for mag in tool_mags:
        try:
            if mag.quantize(quantum, rounding=ROUND_HALF_UP) == target:
                return True
        except InvalidOperation:
            continue
    return False


def ungrounded_numbers(answer: str, steps: list[Step]) -> list[str]:
    """Figures in `answer` not traceable to a tool observation (allowing correct rounding)."""
    grounded: set[Decimal] = set()
    for step in steps:
        grounded |= _extract(step.observation, figures_only=False)
    answer_figures = _extract(answer, figures_only=True)
    ungrounded = [fig for fig in sorted(answer_figures) if not _is_grounded(fig, grounded)]
    return [format(number, "f") for number in ungrounded]
