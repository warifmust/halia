"""Tests for planning (plan-before-execute)."""

from typing import Any

from halia.config.settings import Config
from halia.core.agent import run
from halia.core.planner import make_plan
from halia.providers.base import ChatResult, Message
from halia.skills import default_registry

_CFG = Config(provider="x", model="m", base_url="u", api_key="k")


class FakeProvider:
    """Returns a pre-scripted sequence of ChatResults and records the messages it saw."""

    def __init__(self, results: list[ChatResult]) -> None:
        self._results = results
        self.calls = 0
        self.seen: list[list[Message]] = []

    def chat(self, messages: list[Message], tools: Any = None) -> ChatResult:
        self.seen.append(list(messages))
        result = self._results[self.calls]
        self.calls += 1
        return result


def test_make_plan_returns_plan_text() -> None:
    provider = FakeProvider([ChatResult(content="1. read file\n2. answer", tool_calls=[])])
    plan = make_plan("do a thing", _CFG, provider=provider)
    assert "read file" in plan
    # planner must not receive tools — it plans, it doesn't execute
    assert provider.calls == 1


def test_run_with_plan_drafts_injects_and_records() -> None:
    provider = FakeProvider(
        [
            ChatResult(content="1. use list_files\n2. answer", tool_calls=[]),  # the plan
            ChatResult(content="all done", tool_calls=[]),  # execution answer
        ]
    )
    seen_plans: list[str] = []
    result = run(
        "list things",
        _CFG,
        default_registry(),
        provider=provider,
        plan=True,
        on_plan=seen_plans.append,
    )
    assert result.answer == "all done"
    assert "list_files" in result.plan  # recorded on the result
    assert seen_plans == [result.plan]  # surfaced live
    # the plan was injected into the execution turn's system message
    exec_system = provider.seen[1][0]
    assert exec_system["role"] == "system"
    assert "drafted this plan" in (exec_system["content"] or "")
    assert provider.calls == 2  # one plan call + one execution call


def test_run_without_plan_makes_no_extra_call() -> None:
    provider = FakeProvider([ChatResult(content="direct answer", tool_calls=[])])
    result = run("q", _CFG, default_registry(), provider=provider)  # plan defaults off
    assert result.plan == ""
    assert provider.calls == 1  # no planning call
