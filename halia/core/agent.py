"""The agent core.

`ask` is a single-turn passthrough (quick Q&A). `run` is the ReAct-style loop:
the model may call tools, halia executes them and feeds results back, repeating
until a final answer — bounded by an iteration cap (a Layer-C limit from day one).
`run` returns a `RunResult` (answer + the provenance of every tool step) and can
emit each step live via an `observer`, so a run is auditable, not opaque.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from halia.audit.trace import Step
from halia.config.settings import Config
from halia.conscience.verify import ungrounded_numbers
from halia.core.checkpoint import Checkpoint
from halia.core.planner import make_plan
from halia.providers.base import ChatResult, DeltaObserver, Message, Provider, ToolCall, Usage
from halia.providers.openai_compat import OpenAICompatProvider
from halia.skills.registry import SkillRegistry
from halia.store.database import DB_PATH

# Path to the user-editable persona overlay — injected into every system prompt so
# the user can tune halia's behaviour (e.g. QA e2e framing) without a code change.
PERSONA_PATH = Path.home() / ".halia" / "PERSONA.md"


def persona_overlay() -> str:
    """Read ~/.halia/PERSONA.md if it exists; return it as a prompt block (or '')."""
    try:
        if PERSONA_PATH.is_file():
            text = PERSONA_PATH.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                return (
                    "\n\n[User persona overlay — these instructions supplement "
                    "the built-in prompt and take precedence where they conflict:]\n\n"
                    + text
                )
    except OSError:
        pass
    return ""


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
    "FILES & PATHS: pass paths exactly as the user gives them — a leading ~ is expanded "
    "by the tools, so pass '~/Works/foo' literally. NEVER invent an absolute path or guess "
    "a username or home directory. If a path the user gave cannot be found, call ask_user "
    "for the correct one — do NOT silently fall back to the current directory ('.') or "
    "analyse a different location than the user asked for; working on the wrong target is "
    "worse than pausing to ask. "
    "When the user describes a test or task they'll want to REPEAT (e.g. 'first do "
    "this, then run that, output in this format'), offer to remember it as a reusable "
    "procedure via save_procedure. First gather the required parts — what's tested, the "
    "test data, the action (an endpoint or ordered steps), the output columns, and a "
    "clear pass/fail rule — asking the user for anything missing. Then state plainly "
    "what you'll save and save it once they agree. Never save silently."
)

DEFAULT_MAX_ITERS = 8

# Budget cap: max total tokens per run. 0 = unlimited. Override with HALIA_BUDGET_TOKENS.
try:
    DEFAULT_BUDGET_TOKENS = int(os.environ.get("HALIA_BUDGET_TOKENS", "0"))
except ValueError:
    DEFAULT_BUDGET_TOKENS = 0

# Cap on the conversation history *sent to the model* each turn (a char proxy for
# tokens, ~4 chars/token). The full transcript is still persisted — this only bounds
# what we transmit. Sized for real work (reading a codebase, long QA runs); override
# with HALIA_HISTORY_BUDGET for very large-context models or very long sessions.
try:
    DEFAULT_HISTORY_BUDGET_CHARS = int(os.environ.get("HALIA_HISTORY_BUDGET", "400000"))
except ValueError:
    DEFAULT_HISTORY_BUDGET_CHARS = 400000

# Injected in place of dropped turns when the history is trimmed — so the model KNOWS
# earlier work happened and doesn't gaslight the user with "this is a fresh session".
_TRUNCATION_NOTE: Message = {
    "role": "system",
    "content": (
        "[Context note: this conversation is long, so some EARLIER turns have been "
        "trimmed to fit the window. Anything you did earlier — analyses, files you read, "
        "prior answers — REALLY happened; it is simply not shown here. Do NOT claim there "
        "is 'no prior context' or that this is a fresh session. If you need a specific "
        "detail from earlier that you can't see, ask the user to re-share it.]"
    ),
}


def _msg_chars(message: Message) -> int:
    content = message.get("content") or ""
    tool_calls = message.get("tool_calls") or []
    return len(str(content)) + len(json.dumps(tool_calls))


def _window(messages: list[Message], max_chars: int) -> list[Message]:
    """The system message + the most recent whole turns that fit within `max_chars`.

    Trims only at user-message boundaries, so an assistant tool-call turn always keeps
    its tool responses (splitting them would make an invalid request). If even the last
    turn exceeds the budget, it's still sent whole — better a big call than a broken one.
    When turns are dropped, a truncation note is inserted so the model knows.

    The protected prefix is the LEADING RUN of system messages — the system prompt plus
    any compaction summary note that follows it — so windowing never drops the summary.
    """
    prefix_end = 0
    while prefix_end < len(messages) and messages[prefix_end].get("role") == "system":
        prefix_end += 1
    system = messages[:prefix_end]
    body = messages[prefix_end:]

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
    return system + [_TRUNCATION_NOTE] + body[start:]


# --- Compaction ---------------------------------------------------------------------
# When the sent-context window nears its budget, halia can COMPACT: summarise the older
# turns into one dense note and keep only the recent turns verbatim — instead of hard-
# dropping the oldest (what _window does). The full transcript is preserved by the caller
# (archived in the session) and every tool result stays in the audit trail, so compaction
# only rewrites what is TRANSMITTED — grounding is never lost.

# Fraction of the history budget at which compaction triggers. Early enough that the
# summarisation call itself still fits comfortably. Override with HALIA_COMPACT_AT.
try:
    COMPACT_THRESHOLD = float(os.environ.get("HALIA_COMPACT_AT", "0.85"))
except ValueError:
    COMPACT_THRESHOLD = 0.85

# After a compaction, keep roughly this fraction of the budget as recent verbatim turns.
_COMPACT_KEEP_RECENT = 0.4

# Asked when the window crosses the compaction threshold; return True to compact now,
# False to skip (fall back to plain truncation). The caller owns any "always" memory.
CompactApprover = Callable[[], bool]

# Called with the turns compaction summarised away, so the caller can archive the full
# transcript before the working set is shrunk.
CompactArchiver = Callable[[list[Message]], None]

_COMPACT_SYSTEM = (
    "You are a meticulous note-taker compacting a long assistant/tool conversation so it "
    "fits a smaller context window. Write a DENSE summary of the conversation below that "
    "preserves everything needed to continue the work faithfully:\n"
    "- the user's original task and any constraints or preferences they stated\n"
    "- key decisions, conclusions, and the current state of the work\n"
    "- important facts, figures, file paths, ids, and endpoints — and, for any number or "
    "result, WHICH tool produced it (the raw tool outputs remain in the audit trail)\n"
    "- what has been done versus what is still pending or unresolved\n"
    "Be factual and specific; do NOT invent anything not present below. Output the summary "
    "as plain text (short headings and bullets are fine), nothing else."
)


def _total_chars(messages: list[Message]) -> int:
    return sum(_msg_chars(m) for m in messages)


def _compact_split(body: list[Message], keep_recent_chars: int) -> int:
    """Index into `body` where the KEEP-verbatim tail begins (a user boundary).

    Everything before it is summarised. Returns 0 when there is nothing worth
    summarising (the whole body is within the keep-recent budget).
    """
    total = 0
    start = len(body)
    for i in range(len(body) - 1, -1, -1):
        total += _msg_chars(body[i])
        if total > keep_recent_chars:
            break
        start = i
    # Begin the kept tail at a user boundary so an assistant tool-call turn is never split
    # from its tool responses.
    while start < len(body) and body[start].get("role") != "user":
        start += 1
    return start


def _summarise(provider: Provider, old: list[Message]) -> str:
    """Ask the model for a dense plain-text summary of the `old` turns."""
    transcript = "\n\n".join(
        f"[{m.get('role')}] {m.get('content') or ''}"
        + (f"\n(tool_calls: {json.dumps(m.get('tool_calls'))})" if m.get("tool_calls") else "")
        for m in old
    )
    req: list[Message] = [
        {"role": "system", "content": _COMPACT_SYSTEM},
        {"role": "user", "content": transcript},
    ]
    return (provider.chat(req).content or "").strip()


def compact_history(
    messages: list[Message],
    config: Config,
    provider: Provider | None = None,
    keep_recent_chars: int | None = None,
) -> list[Message]:
    """Compact `messages` IN PLACE: replace older turns with one summary note.

    Keeps the system prompt and the most recent turns verbatim; summarises the middle.
    Returns the turns that were summarised away (for archiving); an empty list means
    nothing was compacted (too little history to help, or an empty summary).
    """
    provider = provider if provider is not None else build_provider(config)
    if keep_recent_chars is None:
        keep_recent_chars = int(_COMPACT_KEEP_RECENT * DEFAULT_HISTORY_BUDGET_CHARS)
    has_system = bool(messages) and messages[0].get("role") == "system"
    system = messages[:1] if has_system else []
    body = messages[len(system):]
    split = _compact_split(body, keep_recent_chars)
    if split <= 0:
        return []  # nothing old enough to summarise
    old, recent = body[:split], body[split:]
    summary = _summarise(provider, old)
    if not summary:
        return []
    note: Message = {
        "role": "system",
        "content": (
            "[Summary of earlier conversation, compacted to save context. The full "
            "transcript is archived and every tool result remains in the audit trail.]\n\n"
            + summary
        ),
    }
    messages[:] = system + [note] + recent
    return old


def _maybe_compact(ctx: _Ctx, messages: list[Message]) -> None:
    """Before a model call: if the window is near full, offer to compact (once per run)."""
    if ctx.compact_suppressed or ctx.compact_approver is None:
        return
    if _total_chars(messages) < ctx.compact_threshold * ctx.history_budget:
        return
    keep = int(_COMPACT_KEEP_RECENT * ctx.history_budget)
    has_system = bool(messages) and messages[0].get("role") == "system"
    body = messages[1:] if has_system else messages
    if _compact_split(body, keep) <= 0:
        ctx.compact_suppressed = True  # only recent turns remain — nothing to gain; stop checking
        return
    if not ctx.compact_approver():
        ctx.compact_suppressed = True  # user declined — don't nag again this run
        return
    if ctx.on_activity is not None:
        ctx.on_activity("compacting")
    dropped = compact_history(messages, ctx.config, ctx.provider, keep_recent_chars=keep)
    if not dropped:
        ctx.compact_suppressed = True
        return
    if ctx.on_compact is not None:
        ctx.on_compact(dropped)


# How many times the conscience may bounce an answer back to reground flagged figures.
DEFAULT_MAX_CORRECTIONS = 1

# Injected when the number-grounding check finds figures no tool produced. The model
# gets one chance to recompute them through tools (calculate/aggregate/reconcile) or
# drop them — turning a warning into a grounded answer.
_CORRECTION_TEMPLATE = (
    "These figures in your answer were not produced by any tool: {figures}. "
    "If they are arithmetic results (totals, averages, percentages), recompute them "
    "using the tools (calculate, aggregate_csv, reconcile_csv, …). "
    "If they are factual data (dates, names, identifiers, counts) that cannot be "
    "computed by a tool, keep them but note they are from general knowledge, not a "
    "tool result. Do NOT remove valid factual information — only recompute things "
    "that should have been calculated."
)

# Called with each Step as it happens (for live display); does not affect the run.
Observer = Callable[[Step], None]

# Called when the agent starts an activity — "" for a model call (thinking), or a tool
# name just before that tool runs. Lets a UI show what halia is doing right now.
ActivityObserver = Callable[[str], None]

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
    # Cumulative token usage across all model calls in this run.
    usage: Usage = field(default_factory=Usage)


class RunLimitError(RuntimeError):
    """Raised when the loop hits its iteration cap without a final answer."""


def build_provider(config: Config) -> Provider:
    """Construct the provider for the given config.

    If HALIA_FALLBACK_PROVIDERS is set (comma-separated provider names), wraps the
    primary in a FallbackProvider that retries with the listed providers on failure.
    Example: HALIA_FALLBACK_PROVIDERS=deepseek,openai
    """
    kind = getattr(config, "provider_kind", "openai_compat")
    primary: Provider
    if kind == "anthropic":
        from halia.providers.anthropic import AnthropicProvider

        primary = AnthropicProvider(
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model,
        )
    else:
        primary = OpenAICompatProvider(
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model,
            auth_header=getattr(config, "auth_header", "Bearer"),
        )

    # Check for fallback providers.
    import os
    fallback_names = os.environ.get("HALIA_FALLBACK_PROVIDERS", "").strip()
    if not fallback_names:
        return primary

    from halia.config.settings import PROVIDERS, read_secret
    from halia.providers.fallback import FallbackProvider

    fallbacks: list[Provider] = [primary]
    for name in fallback_names.split(","):
        name = name.strip().lower()
        if name == config.provider or name not in PROVIDERS:
            continue
        key = read_secret(name)
        if not key:
            continue
        spec = PROVIDERS[name]
        fb_kind = getattr(spec, "provider_kind", "openai_compat")
        if fb_kind == "anthropic":
            from halia.providers.anthropic import AnthropicProvider as AP
            fb: Provider = AP(base_url=spec.base_url, api_key=key, model=spec.default_model)
        else:
            fb = OpenAICompatProvider(
                base_url=spec.base_url, api_key=key,
                model=spec.default_model,
                auth_header=getattr(spec, "auth_header", "Bearer"),
            )
        fallbacks.append(fb)

    return FallbackProvider(fallbacks) if len(fallbacks) > 1 else primary


def ask(
    prompt: str, config: Config, provider: Provider | None = None, extra_system: str = ""
) -> str:
    """Answer a single prompt (one-shot, no tools). `provider` is injectable for tests."""
    provider = provider if provider is not None else build_provider(config)
    messages: list[Message] = [
        {"role": "system", "content": SYSTEM_PROMPT + persona_overlay() + extra_system},
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
    on_delta: DeltaObserver | None = None
    on_activity: ActivityObserver | None = None
    compact_approver: CompactApprover | None = None
    on_compact: CompactArchiver | None = None
    compact_threshold: float = COMPACT_THRESHOLD
    compact_suppressed: bool = False
    budget_tokens: int = 0  # max total tokens per run (0 = unlimited)
    total_usage: Usage = field(default_factory=Usage)  # accumulated across iterations
    # Circuit breaker: per-tool consecutive failure count. Resets on success.
    _tool_failures: dict[str, int] = field(default_factory=dict)
    # Max consecutive failures before a tool is marked unavailable.
    max_tool_failures: int = 3


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
    circuit_notes: list[str] = []
    for tc in calls:
        name = tc["name"]
        # Circuit breaker: skip tools that have failed too many times consecutively.
        if ctx._tool_failures.get(name, 0) >= ctx.max_tool_failures:
            observation = (
                f"circuit breaker: '{name}' has failed {ctx.max_tool_failures} times "
                f"consecutively — skipping. Find an alternative approach."
            )
            step = Step(tool=name, arguments=tc["arguments"], observation=observation)
            steps.append(step)
            if ctx.observer is not None:
                ctx.observer(step)
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": observation})
            circuit_notes.append(name)
            continue
        if ctx.on_activity is not None:
            ctx.on_activity(name)
        observation = _run_tool(ctx.registry, name, tc["arguments"], ctx.approver)
        # Track success/failure for the circuit breaker.
        is_error = (
            observation.startswith("error ")
            or observation.startswith("error:")
            or observation.startswith("denied ")
        )
        if is_error:
            ctx._tool_failures[name] = ctx._tool_failures.get(name, 0) + 1
        else:
            ctx._tool_failures.pop(name, None)  # success resets the counter
        step = Step(tool=name, arguments=tc["arguments"], observation=observation)
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
        paused=True, checkpoint_id=cp.id, usage=ctx.total_usage,
    )


def _loop(
    ctx: _Ctx,
    messages: list[Message],
    steps: list[Step],
    corrections: int,
    iters_used: int,
) -> RunResult:
    """The ReAct loop, shared by `run` and `resume`. Returns a final or paused result."""
    import time as _time

    from halia.audit.logger import log_run_end, log_run_start

    run_start = _time.perf_counter()
    log_run_start(ctx.prompt[:80], ctx.prompt[:200], ctx.config.provider, ctx.config.model)

    tools = ctx.registry.tool_schemas() or None
    while iters_used < ctx.max_iters:
        iters_used += 1
        # Near the budget? Offer to compact older turns before we build the window.
        _maybe_compact(ctx, messages)
        if ctx.on_activity is not None:
            ctx.on_activity("")  # about to call the model (thinking)
        # Send a bounded window of history (full transcript stays in `messages`).
        window = _window(messages, ctx.history_budget)
        if ctx.on_delta is not None:
            result = ctx.provider.chat(window, tools=tools, on_delta=ctx.on_delta)
        else:
            result = ctx.provider.chat(window, tools=tools)

        # Accumulate token usage and check budget cap.
        ctx.total_usage = ctx.total_usage + result.usage
        if ctx.budget_tokens > 0 and ctx.total_usage.total_tokens >= ctx.budget_tokens:
            answer = (result.content or "").strip() or ""
            budget_msg = (
                f"[budget exceeded: {ctx.total_usage.total_tokens:,} / "
                f"{ctx.budget_tokens:,} tokens used]"
            )
            return RunResult(
                answer=answer or budget_msg, steps=steps,
                corrections=corrections, plan=ctx.plan,
                usage=ctx.total_usage,
            )

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
            elapsed = (_time.perf_counter() - run_start) * 1000
            log_run_end(
                ctx.prompt[:80], answer[:200], len(steps),
                ctx.total_usage.total_tokens, corrections, elapsed,
            )
            return RunResult(
                answer=answer, steps=steps, unverified=unverified,
                corrections=corrections, plan=ctx.plan, usage=ctx.total_usage,
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
    compact: bool = False,
    budget_tokens: int = 0,
) -> RunResult:
    """Run the tool-calling loop until a final answer, the iteration cap, or a pause.

    With `plan=True`, halia drafts a short plan first (one extra call) and follows it
    as *guidance* — the loop still adapts. When a final answer contains figures no tool
    produced, the conscience bounces it back (up to `max_corrections` times). With
    `pause_on_approval=True`, a dangerous tool freezes the run into a checkpoint instead
    of prompting — resume it later with `resume()`.

    With `compact=True`, older turns are auto-summarised when the context window nears
    its budget (no prompt — for headless/scheduled runs). Set HALIA_COMPACT_AUTO=true
    to enable by default.
    """
    provider = provider if provider is not None else build_provider(config)

    plan_text = ""
    system_content = SYSTEM_PROMPT + persona_overlay() + extra_system
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
        compact_approver=(lambda: True) if compact else None,
        on_compact=None, budget_tokens=budget_tokens,
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
    on_delta: DeltaObserver | None = None,
    on_activity: ActivityObserver | None = None,
    compact_approver: CompactApprover | None = None,
    on_compact: CompactArchiver | None = None,
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
        pause_on_approval=False, history_budget=history_budget, on_delta=on_delta,
        on_activity=on_activity, compact_approver=compact_approver, on_compact=on_compact,
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
