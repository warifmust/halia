"""Deterministic computer-use eval harness.

Runs a task through halia's real agent loop against a chosen model, then checks the
OUTCOME mechanically (tool-call provenance or desktop state) — no LLM-as-a-judge.

The scorecard keeps two things separate:
- MODEL verdict (`passed`) — did the task's deterministic check succeed.
- HARNESS health (`guard_events`, `guard_breakdown`, `guard_rate`, `harness_note`) —
  how often the loop guards fired. A guard-heavy run is an attribution signal (weak
  model vs. over-aggressive harness), NOT a model failure.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass

from halia.audit.trace import Step
from halia.config.settings import PROVIDERS, Config, read_secret
from halia.core.agent import RunResult, build_provider, run
from halia.skills import DEFAULT_SKILLS, available_backends, build_registry
from halia.skills.registry import SkillRegistry

# Guards are a HARNESS-health metric, not a model score. If a run's guard rate
# (guard events / total actions) is at or above this, flag it for investigation —
# a strong model should almost never trip the guards, so a high rate means either
# the model is under-performing or the harness is over-aggressive.
try:
    GUARD_RATE_WARN = float(os.environ.get("HALIA_EVAL_GUARD_RATE_WARN", "0.25"))
except ValueError:
    GUARD_RATE_WARN = 0.25


class AutoApprove:
    """Approve every gate for unattended eval runs (dangerous tools, reads)."""

    def __call__(self, name: str, arguments: str) -> bool:
        return True

    def check_read(self, name: str, arguments: str) -> bool:
        return True


@dataclass(frozen=True)
class Verdict:
    """A task's deterministic verdict."""

    passed: bool
    details: str = ""
    fabrications: tuple[str, ...] = ()


@dataclass(frozen=True)
class Task:
    """One eval scenario: a prompt, an optional env reset, and a deterministic check."""

    name: str
    prompt: str
    verify: Callable[[RunResult], Verdict]
    setup: Callable[[], None] | None = None
    max_iters: int = 8
    requires_cua: bool = False  # needs a real desktop; skipped when CUA is unavailable


@dataclass(frozen=True)
class TaskOutcome:
    """The scorecard for one model × task run.

    `passed` is the model's goal verdict (task.verify). `guard_events`,
    `guard_breakdown`, `guard_rate`, and `harness_note` are HARNESS-health
    signals — reported separately and never fed into the pass/fail verdict, so
    a guard-heavy run doesn't masquerade as a model failure.
    """

    task: str
    model: str
    passed: bool
    details: str
    actions: int
    tool_errors: int
    guard_events: int
    guard_breakdown: dict[str, int]
    guard_rate: float
    harness_note: str
    fabrications: tuple[str, ...]
    duration_s: float
    answer: str
    skipped: bool = False


def classify_observation(observation: str) -> str:
    """Bucket a tool observation: 'error', 'guard', or 'ok'."""
    text = observation or ""
    if text.startswith("error"):
        return "error"
    if (
        "repetition guard" in text
        or "circuit breaker" in text
        or "exploration guard" in text
        or "screenshot budget" in text
    ):
        return "guard"
    return "ok"


def score_steps(steps: list[Step]) -> tuple[int, int, int]:
    """(actions, tool_errors, guard_events) from a run's step list."""
    errors = sum(1 for s in steps if classify_observation(s.observation) == "error")
    guards = sum(1 for s in steps if classify_observation(s.observation) == "guard")
    return len(steps), errors, guards


def classify_guard(observation: str) -> str | None:
    """Bucket a loop-guard observation into its kind, or None if not a guard."""
    text = observation or ""
    if "repetition guard" in text:
        return "repetition"
    if "circuit breaker" in text:
        return "circuit_breaker"
    if "exploration guard" in text:
        return "exploration"
    if "screenshot budget" in text:
        return "screenshot_budget"
    return None


def guard_breakdown(steps: list[Step]) -> dict[str, int]:
    """Count each kind of loop-guard event in a run's steps (harness health)."""
    counts: dict[str, int] = {}
    for s in steps:
        kind = classify_guard(s.observation)
        if kind:
            counts[kind] = counts.get(kind, 0) + 1
    return counts


def harness_note(actions: int, guard_events: int) -> str:
    """Flag a high guard rate — a signal the harness may be over-aggressive."""
    if actions <= 0:
        return ""
    rate = guard_events / actions
    if rate >= GUARD_RATE_WARN:
        return (
            f"high guard rate ({rate:.0%}): {guard_events}/{actions} actions were "
            "guard events — investigate whether the model or the harness is at fault"
        )
    return ""


def eval_registry() -> SkillRegistry:
    """Default skills minus ask_user — an unattended eval must not block on a human."""
    return build_registry([name for name in DEFAULT_SKILLS if name != "ask_user"])


def resolve_config(provider: str, model: str) -> Config:
    """Build a Config for a candidate model, reading the provider's key from env/secrets."""
    if provider not in PROVIDERS:
        known = ", ".join(sorted(PROVIDERS))
        raise SystemExit(f"unknown provider '{provider}'. Known providers: {known}.")
    spec = PROVIDERS[provider]
    api_key = (
        os.environ.get("HALIA_API_KEY")
        or os.environ.get(spec.key_env)
        or read_secret(provider)
        or ""
    )
    if not api_key:
        raise SystemExit(
            f"no API key for provider '{provider}'. Set {spec.key_env} "
            f"(or HALIA_API_KEY) in your environment, or run `halia setup`."
        )
    return Config(
        provider=provider, model=model, base_url=spec.base_url,
        api_key=api_key, auth_header=spec.auth_header,
        provider_kind=spec.provider_kind,
    )


def run_task(task: Task, config: Config) -> TaskOutcome:
    """Run one task against one model and score it deterministically."""
    if task.requires_cua and "cua" not in available_backends():
        return TaskOutcome(
            task=task.name, model=config.model, passed=False,
            details="skipped: CUA desktop backend unavailable",
            actions=0, tool_errors=0, guard_events=0,
            guard_breakdown={}, guard_rate=0.0, harness_note="",
            fabrications=(), duration_s=0.0, answer="", skipped=True,
        )
    if task.setup is not None:
        task.setup()
    provider = build_provider(config)
    registry = eval_registry()
    started = time.perf_counter()
    result = run(
        task.prompt, config, registry, provider=provider,
        max_iters=task.max_iters, approver=AutoApprove(),
    )
    duration = time.perf_counter() - started
    verdict = task.verify(result)
    actions, errors, guards = score_steps(result.steps)
    breakdown = guard_breakdown(result.steps)
    rate = (guards / actions) if actions else 0.0
    return TaskOutcome(
        task=task.name, model=config.model, passed=verdict.passed,
        details=verdict.details, actions=actions, tool_errors=errors,
        guard_events=guards, guard_breakdown=breakdown, guard_rate=rate,
        harness_note=harness_note(actions, guards),
        fabrications=verdict.fabrications,
        duration_s=duration, answer=result.answer,
    )
