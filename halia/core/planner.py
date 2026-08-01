"""Planning — think before acting.

For a multi-step task, halia can draft a short plan *before* the execution loop
starts. This improves sequencing and reduces skipped steps (e.g. "reconcile, then
total each file, then compute the difference, then summarize").

Deliberately lightweight: the plan is **guidance, not a rigid graph**. It is a
recorded, observable artifact that the fixed ReAct loop follows and may adapt from
— we do NOT execute a hardcoded step sequence (that would re-introduce a workflow
engine and hurt the loop's adaptivity). One extra model call, no new control flow.
"""

from __future__ import annotations

from halia.config.settings import Config
from halia.providers.base import Message, Provider

PLAN_SYSTEM = (
    "You are halia's planner. Given a task, produce a SHORT numbered plan (2–6 steps) "
    "for how you will accomplish it using tools. Each step should name the tool(s) you "
    "will use and why. Prefer tools for anything involving data or arithmetic. Do NOT "
    "execute anything and do NOT give the final answer — output only the plan, terse."
)


def make_plan(
    prompt: str,
    config: Config,
    provider: Provider,
    extra_system: str = "",
) -> str:
    """Draft a short plan for `prompt`. Returns plan text (may be empty on a terse model)."""
    messages: list[Message] = [
        {"role": "system", "content": PLAN_SYSTEM + extra_system},
        {"role": "user", "content": prompt},
    ]
    return (provider.chat(messages).content or "").strip()
