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
# A capitalized word / acronym directly before the number (one space or hyphen, nothing
# else) marks a NAME or VERSION rather than a computed figure — "Sonnet 4.5", "GPT-4.5",
# "Claude 3.5", "Version 4.5", "Table 4.5".
_NAME_PREFIX = re.compile(r"[A-Z][A-Za-z]*[ \-]$")
# ...but only when the number is VERSION-LIKE: a lone single-decimal (4.5, 3.5, 10.2), no
# currency/comma and not a 2-decimal cents figure. This keeps genuine money safe even after
# a capitalized (often sentence-initial) word — "Still 730.50", "Total 42.00", "Balance
# 1,250.00" are all still ground-checked; only true X.Y version tokens are waved through.
_VERSION_LIKE = re.compile(r"\d+\.\d")


def _extract(text: str, figures_only: bool) -> set[Decimal]:
    cleaned = _DATE.sub(" ", text)
    found: set[Decimal] = set()
    for match in _NUM.finditer(cleaned):
        token = match.group()
        # Strip trailing punctuation (comma, period, semicolon) that the regex may
        # capture as part of the token — "1945," is the year 1945, not a figure.
        stripped = token.rstrip(",.;:!")
        if stripped != token:
            token = stripped
        # A "figure" to verify has a decimal point, currency sign, or thousands comma;
        # bare integers (counts, years, ids) are too noisy to check in the answer.
        if figures_only and not any(mark in token for mark in ".$,"):
            continue
        # A version/model/identifier like "Sonnet 4.5" is not a figure to ground-check —
        # but only wave through a version-like X.Y so real money is never skipped.
        if (
            figures_only
            and _VERSION_LIKE.fullmatch(token)
            and _NAME_PREFIX.search(cleaned[: match.start()])
        ):
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
