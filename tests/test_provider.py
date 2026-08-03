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


# --- streaming (on_delta) ---


def _sse(*chunks: dict) -> str:
    """Build an SSE body from OpenAI-style streaming chunks, ending with [DONE]."""
    lines = [f"data: {json.dumps(c)}" for c in chunks]
    lines.append("data: [DONE]")
    return "\n\n".join(lines) + "\n\n"


def test_stream_emits_content_deltas_and_assembles_answer() -> None:
    body = _sse(
        {"choices": [{"delta": {"content": "Hel"}}]},
        {"choices": [{"delta": {"content": "lo"}}]},
        {"choices": [{"delta": {"content": " world"}}]},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, text=body)

    seen: list[str] = []
    result = _provider(handler).chat([{"role": "user", "content": "hi"}], on_delta=seen.append)
    assert seen == ["Hel", "lo", " world"]  # streamed piece by piece
    assert result.content == "Hello world"  # and assembled whole
    assert result.tool_calls == []


def test_stream_assembles_fragmented_tool_calls() -> None:
    body = _sse(
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "function": {"name": "calc", "arguments": '{"a"'}}
        ]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": ":2}"}}
        ]}}]},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    result = _provider(handler).chat([{"role": "user", "content": "hi"}], on_delta=lambda s: None)
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["name"] == "calc"
    assert result.tool_calls[0]["arguments"] == '{"a":2}'  # fragments concatenated


def test_stream_http_error_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(ProviderError):
        _provider(handler).chat([{"role": "user", "content": "hi"}], on_delta=lambda s: None)
