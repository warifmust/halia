"""Tests for OpenAICompatProvider (mocked HTTP — no network)."""

import json

import httpx
import pytest

from halia.providers.base import ProviderError
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
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi there"}}]})

    result = _provider(handler).chat([{"role": "user", "content": "hi"}])
    assert result.content == "hi there"
    assert result.tool_calls == []


def test_chat_parses_tool_calls() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": '{"path": "x"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    result = _provider(handler).chat([{"role": "user", "content": "hi"}])
    assert result.content is None
    assert result.tool_calls[0]["name"] == "read_file"
    assert result.tool_calls[0]["arguments"] == '{"path": "x"}'


def test_non_200_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="bad key")

    with pytest.raises(ProviderError, match="401"):
        _provider(handler).chat([{"role": "user", "content": "hi"}])


def test_empty_reply_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": None}}]})

    with pytest.raises(ProviderError, match="no content"):
        _provider(handler).chat([{"role": "user", "content": "hi"}])
