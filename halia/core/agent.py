"""The agent core.

`ask` is a single-turn passthrough (quick Q&A). `run` is the ReAct-style loop:
the model may call tools, halia executes them and feeds results back, repeating
until a final answer — bounded by an iteration cap (a Layer-C limit from day one).
`run` returns a `RunResult` (answer + the provenance of every tool step) and can
emit each step live via an `observer`, so a run is auditable, not opaque.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from halia.audit.trace import Step
from halia.config.settings import Config
from halia.conscience.verify import ungrounded_numbers
from halia.providers.base import Message, Provider
from halia.providers.openai_compat import OpenAICompatProvider
from halia.skills.registry import SkillRegistry

SYSTEM_PROMPT = (
    "You are halia, a careful, trustworthy assistant. "
    "Be concise and accurate; if you are unsure, say so rather than guessing. "
    "Use the available tools when they help you answer accurately. "
    "NEVER do arithmetic in your head — route every calculation through the "
    "calculate tool so numbers are exact and verifiable. To total or average a "
    "whole CSV column, use aggregate_csv (it reads every row in code), not a "
    "sum of sampled rows."
)

DEFAULT_MAX_ITERS = 8

# Called with each Step as it happens (for live display); does not affect the run.
Observer = Callable[[Step], None]

# Asked (tool name, raw arguments) before a DANGEROUS tool runs; return True to allow.
Approver = Callable[[str, str], bool]


@dataclass
class RunResult:
    """The outcome of a run: the final answer plus the provenance of each step."""

    answer: str
    steps: list[Step] = field(default_factory=list)
    # Figures in the answer that did NOT come from a tool (number-grounding check).
    unverified: list[str] = field(default_factory=list)


class RunLimitError(RuntimeError):
    """Raised when the loop hits its iteration cap without a final answer."""


def build_provider(config: Config) -> Provider:
    """Construct the provider for the given config."""
    return OpenAICompatProvider(
        base_url=config.base_url,
        api_key=config.api_key,
        model=config.model,
    )


def ask(
    prompt: str, config: Config, provider: Provider | None = None, extra_system: str = ""
) -> str:
    """Answer a single prompt (one-shot, no tools). `provider` is injectable for tests."""
    provider = provider if provider is not None else build_provider(config)
    messages: list[Message] = [
        {"role": "system", "content": SYSTEM_PROMPT + extra_system},
        {"role": "user", "content": prompt},
    ]
    return (provider.chat(messages).content or "").strip()


def run(
    prompt: str,
    config: Config,
    registry: SkillRegistry,
    provider: Provider | None = None,
    max_iters: int = DEFAULT_MAX_ITERS,
    observer: Observer | None = None,
    approver: Approver | None = None,
    extra_system: str = "",
) -> RunResult:
    """Run the tool-calling loop until a final answer or the iteration cap."""
    provider = provider if provider is not None else build_provider(config)
    tools = registry.tool_schemas()
    messages: list[Message] = [
        {"role": "system", "content": SYSTEM_PROMPT + extra_system},
        {"role": "user", "content": prompt},
    ]
    steps: list[Step] = []

    for _ in range(max_iters):
        result = provider.chat(messages, tools=tools or None)
        if not result.tool_calls:
            answer = (result.content or "").strip()
            return RunResult(
                answer=answer,
                steps=steps,
                unverified=ungrounded_numbers(answer, steps),
            )

        # Record the assistant's tool-call turn, then execute each call and feed
        # the observations back as `tool` messages.
        messages.append(
            {
                "role": "assistant",
                "content": result.content,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["arguments"]},
                    }
                    for tc in result.tool_calls
                ],
            }
        )
        for tc in result.tool_calls:
            observation = _run_tool(registry, tc["name"], tc["arguments"], approver)
            step = Step(tool=tc["name"], arguments=tc["arguments"], observation=observation)
            steps.append(step)
            if observer is not None:
                observer(step)
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": observation})

    raise RunLimitError(f"hit iteration cap ({max_iters}) without a final answer")


def _run_tool(
    registry: SkillRegistry, name: str, arguments: str, approver: Approver | None
) -> str:
    """Execute one tool call; any failure becomes an observation, never a crash.

    A dangerous skill (run_command, …) is gated: it needs an approver's explicit
    yes. No approver ⇒ blocked (safe default even when called programmatically).
    """
    skill = registry.get(name)
    if skill is None:
        return f"error: unknown tool '{name}'"
    if skill.dangerous:
        if approver is None:
            return f"blocked: '{name}' is dangerous and requires approval, but none is configured"
        if not approver(name, arguments):
            return f"denied by user: '{name}' was not run"
    try:
        parsed: dict[str, Any] = json.loads(arguments) if arguments.strip() else {}
    except json.JSONDecodeError as exc:
        return f"error: invalid tool arguments for '{name}': {exc}"
    if not isinstance(parsed, dict):
        return f"error: tool arguments for '{name}' must be a JSON object"
    try:
        return skill.run(parsed)
    except Exception as exc:  # noqa: BLE001 — tool errors are observations, not crashes
        return f"error running '{name}': {exc}"
