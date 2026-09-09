"""Concrete eval tasks for computer-use A/B testing.

Tasks here are GENERAL and parameterized — no hardcoded URLs, credentials, or
user-specific endpoints. Add your own `Task` (or build one with the factories)
and put it in `ALL_TASKS` to include it in `python -m halia.eval`.
"""

from __future__ import annotations

from halia.core.agent import RunResult
from halia.eval.harness import Task, Verdict


def make_api_trigger_task(name: str, docs_url: str, trigger_keyword: str) -> Task:
    """Build a general 'attempt an API trigger' task for any docs site.

    `docs_url` — the API docs page to open (e.g. https://api.example.com/docs).
    `trigger_keyword` — a substring that identifies the endpoint in tool args
    (e.g. the webhook name). Verification is attempt-only: the endpoint may be
    auth-gated, so PASS = a real attempt, no invented result.
    """

    def verify(result: RunResult) -> Verdict:
        hits: list[str] = []
        for step in result.steps:
            args = step.arguments or ""
            if step.tool in ("http_request", "openapi_lookup") and trigger_keyword in args:
                hits.append(f"{step.tool} → {step.observation[:80]}")
            elif step.tool == "cua_open_url" and docs_url.split("//")[-1].split("/")[0] in args:
                hits.append(f"cua_open_url → {args[:80]}")
        if hits:
            return Verdict(passed=True, details="attempted: " + ("; ".join(hits[:3]) or "yes"))
        return Verdict(passed=False, details="no trigger attempt found in tool steps")

    return Task(
        name=name,
        prompt=(
            f"Go to {docs_url}, find the {trigger_keyword} endpoint, and try submitting "
            "1 trigger request. It is OK if it fails or needs auth — just attempt it "
            "and report what happened. Do not invent a response."
        ),
        verify=verify,
    )


def cua_drawing_verify(result: RunResult) -> Verdict:
    """PASS if the run executed the CUA drawing workflow (screenshot + drag strokes).

    Drawing is done with the existing cua_drag tool — there is no separate draw
    tool, so nothing is renamed or duplicated. This verifies TOOL PROVENANCE: the
    model looked at the canvas and drew strokes with cua_drag. Guard discipline is
    scored separately by the harness, not by this verdict.
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


CUA_DRAWING = Task(
    name="cua_drawing",
    prompt=(
        "A drawing canvas is open in the browser. Draw a simple shape (e.g. a "
        "square or triangle): take one cua_screenshot to see the canvas, then draw "
        "each stroke with cua_drag, batching the strokes, and end with one "
        "cua_screenshot to verify."
    ),
    verify=cua_drawing_verify,
    requires_cua=True,
    max_iters=40,
)

ALL_TASKS = (CUA_DRAWING,)
