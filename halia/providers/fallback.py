"""Fallback provider — retries with the next provider on failure.

Wraps a list of providers and tries each in order. If the primary fails with a
`ProviderError` (rate limit, timeout, 5xx), the next provider is tried. This gives
scheduled/unattended runs resilience against a single provider being down.
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


class FallbackProvider:
    """Try providers in order; fall back on ProviderError."""

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
            try:
                return provider.chat(messages, tools=tools, on_delta=on_delta)
            except ProviderError as exc:
                last_error = exc
                if i + 1 < len(self._providers):
                    from halia.audit.logger import log_fallback

                    log_fallback(
                        type(self._providers[i]).__name__,
                        type(self._providers[i + 1]).__name__,
                        str(exc)[:200],
                    )
                continue
        raise last_error  # type: ignore[misc]
