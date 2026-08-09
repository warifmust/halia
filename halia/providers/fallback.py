"""Fallback provider — retries with the next provider on failure.

Wraps a list of providers and tries each in order. If the primary fails with a
`ProviderError` (rate limit, timeout, 5xx), the next provider is tried. This gives
scheduled/unattended runs resilience against a single provider being down.

Streaming caveat handled: if a provider fails *after* it has already streamed tokens
to the user, we do NOT fall back — retrying would replay those tokens on top of what
the user already saw. Fallback only happens when the failed provider emitted nothing
(the common case: it errored at connect/first-byte). Non-streaming runs always fall
back, since nothing was shown yet.
"""

from __future__ import annotations

from typing import Any

from halia.providers.base import (
    ChatResult,
    DeltaObserver,
    Message,
    Provider,
    ProviderError,
)


def _track(on_delta: DeltaObserver | None) -> tuple[DeltaObserver | None, dict[str, bool]]:
    """Wrap `on_delta` so we can tell whether it emitted anything. Returns (wrapper, state)."""
    state = {"emitted": False}
    if on_delta is None:
        return None, state

    def wrapper(piece: str) -> None:
        state["emitted"] = True
        on_delta(piece)

    return wrapper, state


class FallbackProvider:
    """Try providers in order; fall back on ProviderError (unless mid-stream)."""

    def __init__(self, providers: list[Provider]) -> None:
        if not providers:
            raise ValueError("FallbackProvider requires at least one provider")
        self._providers = providers

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        on_delta: DeltaObserver | None = None,
    ) -> ChatResult:
        last_error: ProviderError | None = None
        for i, provider in enumerate(self._providers):
            wrapped, state = _track(on_delta)
            try:
                return provider.chat(messages, tools=tools, on_delta=wrapped)
            except ProviderError as exc:
                last_error = exc
                # Already streamed partial output to the user → falling back would
                # duplicate it. Surface the error instead of double-emitting.
                if state["emitted"]:
                    raise
                if i + 1 < len(self._providers):
                    from halia.audit.logger import log_fallback

                    log_fallback(
                        type(self._providers[i]).__name__,
                        type(self._providers[i + 1]).__name__,
                        str(exc)[:200],
                    )
                continue
        raise last_error  # type: ignore[misc]
