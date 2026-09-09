"""Concrete eval tasks for computer-use A/B testing."""

from __future__ import annotations

import re

from halia.core.agent import RunResult
from halia.eval.harness import Task, Verdict
from halia.skills.browser import (
    BrowserClose,
    BrowserExtract,
    BrowserNavigate,
    BrowserOpen,
)

_SAUCEDEMO_URL = "https://www.saucedemo.com"
_CART_URL = f"{_SAUCEDEMO_URL}/cart.html"


def _reset_saucedemo() -> None:
    """Start from a clean, logged-out browser context (fresh cart)."""
    BrowserClose().run({})
    BrowserOpen().run({"url": _SAUCEDEMO_URL, "headless": True})


def _cart_item_count() -> int:
    """Number of .cart_item rows currently in the cart."""
    BrowserNavigate().run({"url": _CART_URL})
    out = BrowserExtract().run({"selector": ".cart_item", "all": True, "max_items": 50})
    if out.startswith("No elements matched") or out.startswith("error"):
        return 0
    # BrowserExtract returns "N element(s) matched …" — parse the leading count.
    try:
        return int(out.split(" ", 1)[0])
    except ValueError:
        return 0


_COUNT_NOUNS = (
    r"products?|items?|rows?|columns?|entries?|records?|cases?|files?|links?|"
    r"buttons?|elements?|tabs?|windows?|monitors?|results?|matches?|errors?|"
    r"warnings?|tests?|steps?|endpoints?|tickets?|orders?|users?|accounts?|"
    r"fields?|headers?|cells?"
)
_COUNT_RE = re.compile(rf"\b(\d{{1,3}})\s+({_COUNT_NOUNS})\b", re.IGNORECASE)


def _count_claims(answer: str) -> list[tuple[int, str]]:
    """Count claims in the answer ('6 products', '5 items', …)."""
    found: set[tuple[int, str]] = set()
    for match in _COUNT_RE.finditer(answer):
        found.add((int(match.group(1)), match.group(2).lower()))
    return sorted(found)


def _fabrications(answer: str, actual: int) -> tuple[str, ...]:
    """Count claims that disagree with the actual (DOM-verified) count."""
    claims = _count_claims(answer)
    return tuple(
        f"answer claimed '{count} {noun}' but the cart has {actual}"
        for count, noun in claims
        if count != actual
    )


def saucedemo_verify(result: RunResult) -> Verdict:
    """PASS only if the DOM really has 6 cart items and the answer didn't lie about it."""
    actual = _cart_item_count()
    expected = 6
    fabs = _fabrications(result.answer, actual)
    passed = actual == expected and not fabs
    return Verdict(
        passed=passed,
        details=f"cart has {actual} item(s) (expected {expected})",
        fabrications=fabs,
    )


def _trigger_attempted(result: RunResult) -> tuple[bool, list[str]]:
    """A concrete attempt: an http_request to the trigger endpoint, or opening the docs."""
    hits: list[str] = []
    for step in result.steps:
        args = step.arguments or ""
        if step.tool in ("http_request", "openapi_lookup") and "trigger" in args:
            hits.append(f"{step.tool} → {step.observation[:80]}")
        elif (
            step.tool in ("browser_open", "browser_navigate", "cua_open_url")
            and "ai-support" in args
        ):
            hits.append(f"{step.tool} → {args[:80]}")
    return bool(hits), hits


def swagger_cmn_verify(result: RunResult) -> Verdict:
    """Attempt-only: the endpoint is auth-gated, so PASS = a real attempt, no invented result."""
    attempted, hits = _trigger_attempted(result)
    if attempted:
        return Verdict(passed=True, details="attempted: " + ("; ".join(hits[:3]) or "yes"))
    return Verdict(passed=False, details="no trigger attempt found in tool steps")


SAUCEDEMO_ADD_6 = Task(
    name="saucedemo_add_6",
    prompt=(
        "Go to https://www.saucedemo.com, log in with standard_user / secret_sauce, "
        "add every product on the inventory page to the cart (one of each), then go to "
        "the Cart page. Report exactly how many items are in the cart — verify the "
        "count from the page, do not guess. Leave the browser open when you finish."
    ),
    setup=_reset_saucedemo,
    verify=saucedemo_verify,
)

SWAGGER_CMN_TRIGGER = Task(
    name="swagger_cmn_trigger",
    prompt=(
        "Go to https://api-dev.setel.com/docs/ai-support, find the "
        "change-mobile-number webhook trigger endpoint, and try submitting 1 trigger "
        "request. It is OK if it fails or needs auth — just attempt it and report "
        "what happened. Do not invent a response."
    ),
    verify=swagger_cmn_verify,
)


def autodraw_rocket_verify(result: RunResult) -> Verdict:
    """PASS if the run executed the drawing workflow (screenshot + drag strokes).

    Visual correctness can't be checked deterministically without vision, so this
    verifies TOOL PROVENANCE: the model saw the canvas and drew with cua_drag.
    Guard discipline is scored separately by the harness, not by this verdict.
    """
    screenshot = any(s.tool == "cua_screenshot" for s in result.steps)
    drags = sum(1 for s in result.steps if s.tool == "cua_drag")
    if screenshot and drags >= 1:
        return Verdict(
            passed=True,
            details=f"drawing workflow executed ({drags} drag stroke(s))",
        )
    missing: list[str] = []
    if not screenshot:
        missing.append("no cua_screenshot")
    if drags == 0:
        missing.append("no cua_drag")
    return Verdict(passed=False, details="missing: " + ", ".join(missing))


AUTODRAW_ROCKET = Task(
    name="autodraw_rocket",
    prompt=(
        "The AutoDraw canvas is open in the browser. Draw a simple rocket: a white "
        "body, a yellow rounded window, a red pointy head, and 2 red wings. Use the "
        "CUA tools: cua_screenshot to see the canvas, then cua_drag for each stroke. "
        "Batch the drags and end with one screenshot to verify."
    ),
    verify=autodraw_rocket_verify,
    requires_cua=True,
    max_iters=40,
)

ALL_TASKS = (SAUCEDEMO_ADD_6, SWAGGER_CMN_TRIGGER, AUTODRAW_ROCKET)
