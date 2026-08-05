"""Tests for halia.providers.anthropic."""

from __future__ import annotations

import json

import httpx
import pytest

from halia.providers.anthropic import (
    AnthropicProvider,
    _convert_messages,
    _convert_tools,
    _parse_response,
)
from halia.providers.base import ProviderError


def _provider(handler: object) -> AnthropicProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    return AnthropicProvider(
        base_url="https://api.anthropic.com/v1",
        api_key="test-key",
        model="claude-sonnet-5",
        client=client,
    )


def test_convert_messages_splits_system() -> None:
    """System messages are extracted to the top-level system string."""
    messages = [
        {"role": "system", "content": "You are halia."},
        {"role": "user", "content": "hello"},
    ]
    system, converted = _convert_messages(messages)
    assert system == "You are halia."
    assert converted == [{"role": "user", "content": "hello"}]


def test_convert_messages_multiple_system_joined() -> None:
    """Multiple system messages are joined with blank lines."""
    messages = [
        {"role": "system", "content": "First."},
        {"role": "system", "content": "Second."},
        {"role": "user", "content": "hi"},
    ]
    system, _ = _convert_messages(messages)
    assert system == "First.\n\nSecond."


def test_convert_messages_tool_result() -> None:
    """Tool result messages become tool_result content blocks."""
    messages = [
        {"role": "tool", "tool_call_id": "call_1", "content": "42"},
    ]
    system, converted = _convert_messages(messages)
    assert system == ""
    assert converted == [
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_1", "content": "42"}
            ],
        }
    ]


def test_convert_messages_assistant_tool_calls() -> None:
    """Assistant messages with tool_calls become tool_use blocks."""
    messages = [
        {
            "role": "assistant",
            "content": "Calling tool.",
            "tool_calls": [
                {
                    "id": "call_1",
                    "function": {"name": "calc", "arguments": '{"expr": "1+1"}'},
                }
            ],
        }
    ]
    system, converted = _convert_messages(messages)
    assert converted[0]["role"] == "assistant"
    assert converted[0]["content"] == [
        {"type": "text", "text": "Calling tool."},
        {"type": "tool_use", "id": "call_1", "name": "calc", "input": {"expr": "1+1"}},
    ]


def test_convert_tools() -> None:
    """OpenAI-style tools are converted to Anthropic input_schema format."""
    tools = [
        {
            "type": "function",
            "function": {
                "name": "calc",
                "description": "Calculate",
                "parameters": {"type": "object", "properties": {"expr": {"type": "string"}}},
            },
        }
    ]
    assert _convert_tools(tools) == [
        {
            "name": "calc",
            "description": "Calculate",
            "input_schema": {
                "type": "object",
                "properties": {"expr": {"type": "string"}},
            },
        }
    ]


def test_parse_response_text_only() -> None:
    """A plain text response is parsed into ChatResult."""
    data = {"content": [{"type": "text", "text": "Hello!"}]}
    result = _parse_response(data)
    assert result.content == "Hello!"
    assert result.tool_calls == []


def test_parse_response_tool_use() -> None:
    """A tool_use block is parsed into a ToolCall with JSON arguments."""
    data = {
        "content": [
            {"type": "tool_use", "id": "call_1", "name": "calc", "input": {"expr": "1+1"}}
        ]
    }
    result = _parse_response(data)
    assert result.content is None
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["id"] == "call_1"
    assert result.tool_calls[0]["name"] == "calc"
    assert json.loads(result.tool_calls[0]["arguments"]) == {"expr": "1+1"}


def test_parse_response_empty_raises() -> None:
    """An empty response raises ProviderError."""
    with pytest.raises(ProviderError):
        _parse_response({"content": []})


def test_chat_once_success() -> None:
    """chat() returns content for a successful non-streaming response."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/messages"
        assert request.headers["x-api-key"] == "test-key"
        assert request.headers["anthropic-version"]
        body = json.loads(request.content)
        assert body["model"] == "claude-sonnet-5"
        assert "stream" not in body
        return httpx.Response(200, json={"content": [{"type": "text", "text": "Hi!"}]})

    result = _provider(handler).chat([{"role": "user", "content": "hello"}])
    assert result.content == "Hi!"


def test_chat_once_http_error() -> None:
    """chat() raises ProviderError on non-200 status."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid key")

    with pytest.raises(ProviderError, match="HTTP 401"):
        _provider(handler).chat([{"role": "user", "content": "hello"}])


def test_chat_stream_text() -> None:
    """Streaming text response accumulates content and emits deltas."""
    deltas: list[str] = []
    lines = [
        'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello"}}',
        'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "!"}}',
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        body = "\n".join(lines).encode()
        return httpx.Response(200, content=body)

    result = _provider(handler).chat(
        [{"role": "user", "content": "hello"}],
        on_delta=deltas.append,
    )
    assert result.content == "Hello!"
    assert deltas == ["Hello", "!"]
