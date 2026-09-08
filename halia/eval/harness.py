"""Deterministic computer-use eval harness.

Runs a task through halia's real agent loop against a chosen model, then checks the
OUTCOME mechanically (browser DOM state or tool-call provenance) — no LLM-as-a-judge.
Scores goal-reached, action count, tool errors, loop-guard events, and count-claim
fabrications so candidate models can be A/B tested on the same tasks.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass

from halia.audit.trace import Step
from halia.config.settings import PROVIDERS, Config, read_secret
from halia.core.agent import RunResult, build_provider, run
from halia.skills import DEFAULT_SKILLS, build_registry
from halia.skills.registry import SkillRegistry


class AutoApprove:
    """Approve every gate for unattended eval runs (consent, dangerous tools, reads)."""

    def __call__(self, name: str, arguments: str) -> bool:
        return True

    def check_consent(self, name: str) -> bool:
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


@dataclass(frozen=True)
class TaskOutcome:
    """The scorecard for one model × task run."""

    task: str
    model: str
    passed: bool
    details: str
    actions: int
    tool_errors: int
    guard_events: int
    fabrications: tuple[str, ...]
    duration_s: float
    answer: str


def classify_observation(observation: str) -> str:
    """Bucket a tool observation: 'error', 'guard', or 'ok'."""
    text = observation or ""
    if text.startswith("error"):
        return "error"
    if (
        "repetition guard" in text
        or "circuit breaker" in text
        or "exploration guard" in text
    ):
        return "guard"
    return "ok"


def score_steps(steps: list[Step]) -> tuple[int, int, int]:
    """(actions, tool_errors, guard_events) from a run's step list."""
    errors = sum(1 for s in steps if classify_observation(s.observation) == "error")
    guards = sum(1 for s in steps if classify_observation(s.observation) == "guard")
    return len(steps), errors, guards


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
    return TaskOutcome(
        task=task.name, model=config.model, passed=verdict.passed,
        details=verdict.details, actions=actions, tool_errors=errors,
        guard_events=guards, fabrications=verdict.fabrications,
        duration_s=duration, answer=result.answer,
    )
