"""Rough token-cost estimation for the `/cost` command.

The token counts halia reports are EXACT — they come from the provider's `usage`
field. Dollar figures are only ever ESTIMATES: model prices change and differ by
provider, so treat them as a ballpark, not a bill.

The built-in prices below are rough. Override any of them (or add your own model)
in `~/.halia/config.json`:

    { "prices": { "my-model": {"in": 1.0, "out": 3.0} } }   # USD per 1M tokens

Keys are matched as case-insensitive substrings of the model name, longest match
wins — so "claude-opus" covers "claude-opus-5", "claude-opus-4.8", etc.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

# USD per 1,000,000 tokens, (input, output). ROUGH ESTIMATES — edit in config.
_BUILTIN_PRICES: dict[str, dict[str, float]] = {
    # small / cheap
    "gpt-4.1-nano": {"in": 0.10, "out": 0.40},
    "gpt-4o-mini": {"in": 0.15, "out": 0.60},
    "deepseek": {"in": 0.30, "out": 1.20},
    "mimo": {"in": 0.30, "out": 1.20},
    "haiku": {"in": 0.80, "out": 4.0},
    # mid
    "gpt-4o": {"in": 2.5, "out": 10.0},
    "sonnet": {"in": 3.0, "out": 15.0},
    "gpt-5.2": {"in": 5.0, "out": 15.0},
    # premium
    "o1": {"in": 15.0, "out": 60.0},
    "gpt-5.2-pro": {"in": 15.0, "out": 60.0},
    "opus": {"in": 15.0, "out": 75.0},
}


def _merged_prices(overrides: dict[str, Any] | None) -> dict[str, dict[str, float]]:
    merged: dict[str, dict[str, float]] = {k.lower(): v for k, v in _BUILTIN_PRICES.items()}
    if overrides:
        for key, val in overrides.items():
            if isinstance(val, dict) and "in" in val and "out" in val:
                try:
                    merged[str(key).lower()] = {"in": float(val["in"]), "out": float(val["out"])}
                except (TypeError, ValueError):
                    continue
    return merged


def price_for(model: str, overrides: dict[str, Any] | None = None) -> dict[str, float] | None:
    """The (in, out) price per 1M tokens for `model`, or None if unknown.

    Matches price keys as case-insensitive substrings of the model name; the longest
    matching key wins, so a specific entry beats a general one.
    """
    m = model.lower()
    matches = [(k, v) for k, v in _merged_prices(overrides).items() if k in m]
    if not matches:
        return None
    return max(matches, key=lambda kv: len(kv[0]))[1]


def estimate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    overrides: dict[str, Any] | None = None,
    cached_tokens: int = 0,
) -> Decimal | None:
    """Estimated USD cost for the given token usage, or None if the model has no price.

    Cached prompt tokens are billed at the cheaper cache rate — `price["cached"]` if set,
    else a rough 10% of the input rate. Pass `cached_tokens` (from usage) to reflect it.
    """
    price = price_for(model, overrides)
    if price is None:
        return None
    in_rate = Decimal(str(price["in"]))
    out_rate = Decimal(str(price["out"]))
    cached_rate = Decimal(str(price["cached"])) if "cached" in price else in_rate / 10
    cached = max(0, min(cached_tokens, prompt_tokens))
    full_in = prompt_tokens - cached
    cost = (
        Decimal(full_in) * in_rate
        + Decimal(cached) * cached_rate
        + Decimal(completion_tokens) * out_rate
    ) / Decimal(1_000_000)
    return cost
