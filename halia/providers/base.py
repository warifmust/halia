"""Provider abstraction.

A `Provider` turns a list of chat messages into a reply string. The concrete
`OpenAICompatProvider` covers OpenAI / DeepSeek / OpenRouter (all OpenAI-compatible);
an `AnthropicProvider` comes later. We own this thin interface deliberately — no
fat aggregator (litellm) — to keep the dependency surface small and auditable.
"""

from __future__ import annotations

from typing import Protocol, TypedDict


class Message(TypedDict):
    """A single chat message."""

    role: str
    content: str


class ProviderError(RuntimeError):
    """Raised when a provider call fails or returns an unexpected shape."""


class Provider(Protocol):
    """Anything that can turn chat messages into a reply."""

    def chat(self, messages: list[Message]) -> str:
        """Send `messages` to the model and return the reply text."""
        ...
