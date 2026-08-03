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
from pathlib import Path
from typing import Any

from halia.audit.trace import Step
from halia.config.settings import Config
from halia.conscience.verify import ungrounded_numbers
from halia.core.checkpoint import Checkpoint
from halia.core.planner import make_plan
from halia.providers.base import ChatResult, Message, Provider, ToolCall
from halia.providers.openai_compat import OpenAICompatProvider
from halia.skills.registry import SkillRegistry
from halia.store.database import DB_PATH

SYSTEM_PROMPT = (
    "You are halia, a careful, trustworthy assistant. "
    "Be concise and accurate; if you are unsure, say so rather than guessing. "
    "The conversation history you are given is your memory of this session — rely on "
    "it, and refer back to earlier messages naturally. Do NOT claim you have no memory "
    "or that 'each session starts fresh' when earlier turns are present in the "
    "conversation; that history is real and yours to use. "
    "Use the available tools when they help you answer accurately. "
    "NEVER do arithmetic in your head — route every calculation through the "
    "calculate tool so numbers are exact and verifiable. To total or average a "
    "whole CSV column, use aggregate_csv (it reads every row in code), not a "
    "sum of sampled rows. "
    "When the user describes a test or task they'll want to REPEAT (e.g. 'first do "
    "this, then run that, output in this format'), offer to remember it as a reusable "
    "procedure via save_procedure. First gather the required parts — what's tested, the "
    "test data, the action (an endpoint or ordered steps), the output columns, and a "
    "clear pass/fail rule — asking the user for anything missing. Then state plainly "
    "what you'll save and save it once they agree. Never save silently."
)

DEFAULT_MAX_ITERS = 8

# Cap on the conversation history *sent to the model* each turn (a char proxy for
# tokens, ~4 chars/token). The full transcript is still persisted — this only bounds
# what we transmit, so long chats don't bloat cost or overflow the context window.
DEFAULT_HISTORY_BUDGET_CHARS = 40000


def _msg_chars(message: Message) -> int:
    content = message.get("content") or ""
    tool_calls = message.get("tool_calls") or []
    return len(str(content)) + len(json.dumps(tool_calls))


def _window(messages: list[Message], max_chars: int) -> list[Message]:
    """The system message + the most recent whole turns that fit within `max_chars`.

    Trims only at user-message boundaries, so an assistant tool-call turn always keeps
    its tool responses (splitting them would make an invalid request). If even the last
    turn exceeds the budget, it's still sent whole — better a big call than a broken one.
    """
    has_system = bool(messages) and messages[0].get("role") == "system"
    system = messages[:1] if has_system else []
    body = messages[len(system):]

    total = 0
    start = len(body)  # nothing kept yet
    for i in range(len(body) - 1, -1, -1):
        total += _msg_chars(body[i])
        if total > max_chars:
            break
        start = i
    # Advance to the next user boundary so the window never begins mid-turn.
    while start < len(body) and body[start].get("role") != "user":
        start += 1
    if start >= len(body):  # budget too small for even one whole turn — keep the last one
        start = next(
            (j for j in range(len(body) - 1, -1, -1) if body[j].get("role") == "user"),
            0,
        )
    if start == 0:
        return messages  # everything fits — unchanged
    return system + body[start:]

# How many times the conscience may bounce an answer back to reground flagged figures.
DEFAULT_MAX_CORRECTIONS = 1

# Injected when the number-grounding check finds figures no tool produced. The model
# gets one chance to recompute them through tools (calculate/aggregate/reconcile) or
# drop them — turning a warning into a grounded answer.
_CORRECTION_TEMPLATE = (
    "STOP. These figures in your answer were not produced by any tool: {figures}. "
    "You may have computed them in your head, which is not allowed. Recompute each one "
    "using the tools (calculate, aggregate_csv, reconcile_csv, …), then give the "
    "corrected final answer. If a figure cannot be grounded in a tool result, remove it "
    "rather than assert it."
)

# Called with each Step as it happens (for live display); does not affect the run.
Observer = Callable[[Step], None]

# Called once with the drafted plan text (for live display), before the loop runs.
PlanObserver = Callable[[str], None]

# Asked (tool name, raw arguments) before a DANGEROUS tool runs; return True to allow.
Approver = Callable[[str, str], bool]


@dataclass
class RunResult:
    """The outcome of a run: the final answer plus the provenance of each step."""

    answer: str
    steps: list[Step] = field(default_factory=list)
    # Figures in the answer that did NOT come from a tool (number-grounding check).
    unverified: list[str] = field(default_factory=list)
    # How many corrective passes the conscience triggered to reground flagged figures.
    corrections: int = 0
    # The up-front plan, if planning was enabled (empty otherwise).
    plan: str = ""
    # Set when the run paused for approval instead of finishing (answer is empty then).
    paused: bool = False
    checkpoint_id: str = ""


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


@dataclass
class _Ctx:
    """Everything the loop needs to run, pause, and (later) resume."""

    provider: Provider
    config: Config
    registry: SkillRegistry
    prompt: str
    extra_system: str
    plan: str
    max_iters: int
    max_corrections: int
    observer: Observer | None
    approver: Approver | None
    pause_on_approval: bool
    checkpoint_db: Path = DB_PATH
    history_budget: int = DEFAULT_HISTORY_BUDGET_CHARS


def _is_dangerous(registry: SkillRegistry, name: str) -> bool:
    skill = registry.get(name)
    return skill is not None and skill.dangerous


def _assistant_tool_msg(result: ChatResult) -> Message:
    """The assistant turn recording the tool calls it wants executed."""
    return {
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


def _execute_batch(
    ctx: _Ctx, calls: list[ToolCall], messages: list[Message], steps: list[Step]
) -> None:
    """Run a tool-call batch, appending each step + its `tool` message (in place)."""
    for tc in calls:
        observation = _run_tool(ctx.registry, tc["name"], tc["arguments"], ctx.approver)
        step = Step(tool=tc["name"], arguments=tc["arguments"], observation=observation)
        steps.append(step)
        if ctx.observer is not None:
            ctx.observer(step)
        messages.append({"role": "tool", "tool_call_id": tc["id"], "content": observation})


def _pause(
    ctx: _Ctx,
    messages: list[Message],
    steps: list[Step],
    pending: list[ToolCall],
    iters_used: int,
    corrections: int,
) -> RunResult:
    """Freeze the loop into a checkpoint and return a paused result."""
    from halia.core.checkpoint import new_checkpoint, save_checkpoint

    dangerous = [tc["name"] for tc in pending if _is_dangerous(ctx.registry, tc["name"])]
    cp = new_checkpoint(
        prompt=ctx.prompt,
        provider=ctx.config.provider,
        model=ctx.config.model,
        skills=[s.name for s in ctx.registry.all()],
        extra_system=ctx.extra_system,
        plan=ctx.plan,
        messages=messages,
        steps=steps,
        pending=pending,
        iters_used=iters_used,
        corrections=corrections,
        reason="approval required: " + ", ".join(dangerous),
    )
    save_checkpoint(cp, db_path=ctx.checkpoint_db)
    return RunResult(
        answer="", steps=steps, corrections=corrections, plan=ctx.plan,
        paused=True, checkpoint_id=cp.id,
    )


def _loop(
    ctx: _Ctx,
    messages: list[Message],
    steps: list[Step],
    corrections: int,
    iters_used: int,
) -> RunResult:
    """The ReAct loop, shared by `run` and `resume`. Returns a final or paused result."""
    tools = ctx.registry.tool_schemas() or None
    while iters_used < ctx.max_iters:
        iters_used += 1
        # Send a bounded window of history (full transcript stays in `messages`).
        result = ctx.provider.chat(_window(messages, ctx.history_budget), tools=tools)

        if not result.tool_calls:
            answer = (result.content or "").strip()
            unverified = ungrounded_numbers(answer, steps)
            if unverified and corrections < ctx.max_corrections:
                corrections += 1
                messages.append({"role": "assistant", "content": answer})
                messages.append(
                    {
                        "role": "user",
                        "content": _CORRECTION_TEMPLATE.format(figures=", ".join(unverified)),
                    }
                )
                continue
            return RunResult(
                answer=answer, steps=steps, unverified=unverified,
                corrections=corrections, plan=ctx.plan,
            )

        # A dangerous tool with pausing on ⇒ freeze here for a human decision.
        if ctx.pause_on_approval and any(
            _is_dangerous(ctx.registry, tc["name"]) for tc in result.tool_calls
        ):
            messages.append(_assistant_tool_msg(result))
            return _pause(ctx, messages, steps, result.tool_calls, iters_used, corrections)

        messages.append(_assistant_tool_msg(result))
        _execute_batch(ctx, result.tool_calls, messages, steps)

    raise RunLimitError(f"hit iteration cap ({ctx.max_iters}) without a final answer")


def run(
    prompt: str,
    config: Config,
    registry: SkillRegistry,
    provider: Provider | None = None,
    max_iters: int = DEFAULT_MAX_ITERS,
    max_corrections: int = DEFAULT_MAX_CORRECTIONS,
    observer: Observer | None = None,
    approver: Approver | None = None,
    extra_system: str = "",
    plan: bool = False,
    on_plan: PlanObserver | None = None,
    pause_on_approval: bool = False,
    checkpoint_db: Path = DB_PATH,
) -> RunResult:
    """Run the tool-calling loop until a final answer, the iteration cap, or a pause.

    With `plan=True`, halia drafts a short plan first (one extra call) and follows it
    as *guidance* — the loop still adapts. When a final answer contains figures no tool
    produced, the conscience bounces it back (up to `max_corrections` times). With
    `pause_on_approval=True`, a dangerous tool freezes the run into a checkpoint instead
    of prompting — resume it later with `resume()`.
    """
    provider = provider if provider is not None else build_provider(config)

    plan_text = ""
    system_content = SYSTEM_PROMPT + extra_system
    if plan:
        plan_text = make_plan(prompt, config, provider, extra_system=extra_system)
        if plan_text:
            if on_plan is not None:
                on_plan(plan_text)
            system_content += (
                "\n\nYou drafted this plan for the task:\n"
                f"{plan_text}\n\n"
                "Follow it, adapting as needed. Execute now using tools; do not restate the plan."
            )

    messages: list[Message] = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": prompt},
    ]
    ctx = _Ctx(
        provider=provider, config=config, registry=registry, prompt=prompt,
        extra_system=extra_system, plan=plan_text, max_iters=max_iters,
        max_corrections=max_corrections, observer=observer, approver=approver,
        pause_on_approval=pause_on_approval, checkpoint_db=checkpoint_db,
    )
    return _loop(ctx, messages, [], 0, 0)


def resume(
    checkpoint: Checkpoint,
    config: Config,
    approve: bool,
    provider: Provider | None = None,
    registry: SkillRegistry | None = None,
    observer: Observer | None = None,
    max_iters: int = DEFAULT_MAX_ITERS,
    pause_on_approval: bool = True,
    checkpoint_db: Path = DB_PATH,
) -> RunResult:
    """Resume a paused run: apply the approve/deny decision to the pending batch, continue.

    `config` supplies the api key (never stored in the checkpoint). The registry is
    rebuilt from the checkpoint's skills unless one is passed in.
    """
    from halia.skills import build_registry

    provider = provider if provider is not None else build_provider(config)
    registry = registry if registry is not None else build_registry(checkpoint.skills)

    ctx = _Ctx(
        provider=provider, config=config, registry=registry, prompt=checkpoint.prompt,
        extra_system=checkpoint.extra_system, plan=checkpoint.plan, max_iters=max_iters,
        max_corrections=DEFAULT_MAX_CORRECTIONS,
        observer=observer,
        approver=lambda name, args: approve,  # the human's decision, applied to the batch
        pause_on_approval=pause_on_approval, checkpoint_db=checkpoint_db,
    )

    messages = list(checkpoint.messages)
    steps = list(checkpoint.steps)
    # Complete the frozen tool batch with the decision applied, then continue the loop.
    _execute_batch(ctx, checkpoint.pending, messages, steps)
    return _loop(ctx, messages, steps, checkpoint.corrections, checkpoint.iters_used)


def converse(
    messages: list[Message],
    config: Config,
    registry: SkillRegistry,
    provider: Provider | None = None,
    max_iters: int = DEFAULT_MAX_ITERS,
    observer: Observer | None = None,
    approver: Approver | None = None,
    history_budget: int = DEFAULT_HISTORY_BUDGET_CHARS,
) -> RunResult:
    """Run one chat turn over an existing conversation (the multi-turn / chat primitive).

    Unlike `run` (which builds a fresh [system, user] pair), `converse` continues the
    caller-owned `messages` — which must already hold the system prompt, prior turns,
    and the latest user message. The list is extended in place with the turn's tool
    exchanges; the caller appends the returned answer as the next assistant turn.

    Approval is synchronous here (interactive human present) — no checkpointing.
    """
    provider = provider if provider is not None else build_provider(config)
    prompt = str(messages[-1].get("content", "")) if messages else ""
    ctx = _Ctx(
        provider=provider, config=config, registry=registry, prompt=prompt,
        extra_system="", plan="", max_iters=max_iters,
        max_corrections=DEFAULT_MAX_CORRECTIONS, observer=observer, approver=approver,
        pause_on_approval=False, history_budget=history_budget,
    )
    return _loop(ctx, messages, [], 0, 0)


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
