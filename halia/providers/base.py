"""Provider abstraction.

A `Provider` turns chat messages (+ optional tool schemas) into a `ChatResult` —
either final text or a set of tool calls the agent loop should execute. The
concrete `OpenAICompatProvider` covers OpenAI / DeepSeek / OpenRouter (all
OpenAI-compatible); an `AnthropicProvider` comes later. We own this thin
interface deliberately — no fat aggregator (litellm) — for a small, auditable
dependency surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, TypedDict

# A chat message is just JSON going to the API; its shape varies (system/user,
# assistant-with-tool_calls, tool-result), so a permissive dict is the pragmatic type.
Message = dict[str, Any]


class ToolCall(TypedDict):
    """A single tool call the model wants executed."""

    id: str
    name: str
    arguments: str  # raw JSON string of the arguments


@dataclass(frozen=True)
class ChatResult:
    """One model turn: final `content`, and/or `tool_calls` to execute."""

    content: str | None
    tool_calls: list[ToolCall]


class ProviderError(RuntimeError):
    """Raised when a provider call fails or returns an unexpected shape."""


class Provider(Protocol):
    """Anything that can turn chat messages into a `ChatResult`."""

    def chat(
        self, messages: list[Message], tools: list[dict[str, Any]] | None = None
    ) -> ChatResult:
        """Send `messages` (+ optional `tools`) and return the model's turn."""
        ...
