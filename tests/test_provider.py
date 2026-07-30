"""Tests for OpenAICompatProvider (mocked HTTP — no network)."""

import json

import httpx
import pytest

from halia.providers.base import Message, ProviderError
from halia.providers.openai_compat import OpenAICompatProvider


def _provider(handler: object) -> OpenAICompatProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    return OpenAICompatProvider(base_url="https://x/v1", api_key="k", model="m", client=client)


def test_chat_returns_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer k"
        body = json.loads(request.content)
        assert body["model"] == "m"
        assert body["stream"] is False
        return httpx.Response(200, json={"choices": [{"message": {"content": " hi there "}}]})

    provider = _provider(handler)
    messages: list[Message] = [{"role": "user", "content": "hi"}]
    assert provider.chat(messages) == "hi there"


def test_non_200_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="bad key")

    provider = _provider(handler)
    with pytest.raises(ProviderError, match="401"):
        provider.chat([{"role": "user", "content": "hi"}])


def test_unexpected_shape_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"nope": True})

    provider = _provider(handler)
    with pytest.raises(ProviderError, match="unexpected response shape"):
        provider.chat([{"role": "user", "content": "hi"}])
