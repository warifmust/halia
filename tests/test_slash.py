"""Tests for chat slash-command pure helpers and cost estimation."""

from __future__ import annotations

from decimal import Decimal

from halia.cli.slash import (
    available_models,
    conversation_markdown,
    drop_last_exchange,
    format_history,
    human_count,
)
from halia.pricing import estimate_cost, price_for
from halia.providers.base import Message


def _convo() -> list[Message]:
    return [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
        {"role": "user", "content": "second question"},
        {"role": "assistant", "content": "second answer"},
    ]


# ── format_history ──────────────────────────────────────────────────────────────


def test_format_history_skips_system_and_limits() -> None:
    out = format_history(_convo(), n=2)
    assert "sys" not in out
    assert "second question" in out and "second answer" in out
    assert "hello" not in out  # trimmed to last 2


def test_format_history_empty() -> None:
    assert "no conversation" in format_history([{"role": "system", "content": "x"}])


# ── conversation_markdown ───────────────────────────────────────────────────────


def test_conversation_markdown_has_turns_and_meta() -> None:
    md = conversation_markdown(_convo(), title="T", meta={"session": "abc"})
    assert md.startswith("# T")
    assert "**session**: abc" in md
    assert "## You" in md and "## halia" in md
    assert "sys" not in md  # system omitted


# ── drop_last_exchange (/undo) ──────────────────────────────────────────────────


def test_drop_last_exchange_removes_last_pair() -> None:
    msgs = _convo()
    removed = drop_last_exchange(msgs)
    assert removed == 2  # last user + its assistant
    assert [m["role"] for m in msgs] == ["system", "user", "assistant"]
    assert msgs[1]["content"] == "hello"


def test_drop_last_exchange_keeps_system_only() -> None:
    msgs: list[Message] = [{"role": "system", "content": "sys"}]
    assert drop_last_exchange(msgs) == 0
    assert len(msgs) == 1


def test_drop_last_exchange_removes_trailing_tool_turns() -> None:
    msgs: list[Message] = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t"}]},
        {"role": "tool", "tool_call_id": "t", "content": "result"},
        {"role": "assistant", "content": "a2"},
    ]
    removed = drop_last_exchange(msgs)
    assert removed == 4  # u2 + assistant(tool_calls) + tool + a2
    assert [m["role"] for m in msgs] == ["system", "user", "assistant"]


# ── available_models ────────────────────────────────────────────────────────────


def test_available_models_known_provider_no_sentinel() -> None:
    models = available_models("openrouter")
    assert models  # non-empty
    assert "Custom model…" not in models  # picker sentinel filtered out


def test_available_models_unknown_provider() -> None:
    assert available_models("nope") == []


# ── pricing (/cost estimate) ────────────────────────────────────────────────────


def test_estimate_cost_known_model() -> None:
    # gpt-4o-mini: $0.15/1M in, $0.60/1M out → 1M in + 1M out = 0.15 + 0.60 = 0.75
    cost = estimate_cost("gpt-4o-mini", 1_000_000, 1_000_000)
    assert cost == Decimal("0.75")


def test_estimate_cost_unknown_model_is_none() -> None:
    assert estimate_cost("some-obscure-model", 1000, 1000) is None


def test_price_longest_match_wins() -> None:
    # "gpt-4o-mini" must beat the shorter "gpt-4o" substring.
    assert price_for("gpt-4o-mini") == {"in": 0.15, "out": 0.60}
    assert price_for("gpt-4o") == {"in": 2.5, "out": 10.0}


def test_price_override_from_config() -> None:
    overrides = {"my-model": {"in": 1.0, "out": 2.0}}
    cost = estimate_cost("my-model", 1_000_000, 1_000_000, overrides)
    assert cost == Decimal("3.0")


def test_estimate_cost_cached_is_cheaper() -> None:
    full = estimate_cost("gpt-4o-mini", 1000, 0)
    cached = estimate_cost("gpt-4o-mini", 1000, 0, cached_tokens=1000)
    assert full is not None and cached is not None
    assert cached < full  # cached tokens billed at the cheaper cache rate


# ── human_count (abbreviated token display) ─────────────────────────────────────


def test_human_count() -> None:
    assert human_count(0) == "0"
    assert human_count(842) == "842"
    assert human_count(9594) == "9.6k"
    assert human_count(19000) == "19k"       # trailing .0 dropped
    assert human_count(19196) == "19.2k"
    assert human_count(100000) == "100k"
    assert human_count(1_200_000) == "1.2M"
