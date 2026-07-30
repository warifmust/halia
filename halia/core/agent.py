"""The agent core.

For now this is a single-turn `ask` — the thinnest end-to-end path (config →
provider → reply) that proves the wiring. The real ReAct loop (tool calls,
verification, audit) grows here as the layers land.
"""

from __future__ import annotations

from halia.config.settings import Config
from halia.providers.base import Message, Provider
from halia.providers.openai_compat import OpenAICompatProvider

SYSTEM_PROMPT = (
    "You are halia, a careful, trustworthy assistant. "
    "Be concise and accurate; if you are unsure, say so rather than guessing."
)


def build_provider(config: Config) -> Provider:
    """Construct the provider for the given config."""
    return OpenAICompatProvider(
        base_url=config.base_url,
        api_key=config.api_key,
        model=config.model,
    )


def ask(prompt: str, config: Config, provider: Provider | None = None) -> str:
    """Answer a single prompt (one-shot). `provider` is injectable for tests."""
    provider = provider if provider is not None else build_provider(config)
    messages: list[Message] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    return provider.chat(messages)
