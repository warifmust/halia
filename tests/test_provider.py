"""Tests for OpenAICompatProvider (mocked HTTP — no network)."""

import json

import httpx
import pytest

from halia.providers.base import ProviderError, Usage
from halia.providers.openai_compat import OpenAICompatProvider


def _provider(handler: object) -> OpenAICompatProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    return OpenAICompatProvider(base_url="https://x/v1", api_key="k", model="m", client=client)


def _provider_retry(
    handler: object,
    max_retries: int = 0,
    retry_base: float = 0.0,
    retry_cap: float = 0.0,
    empty_retries: int = 0,
) -> OpenAICompatProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    return OpenAICompatProvider(
        base_url="https://x/v1", api_key="k", model="m", client=client,
        max_retries=max_retries, retry_base=retry_base, retry_cap=retry_cap,
        empty_retries=empty_retries,
    )


def test_parse_usage_captures_cached_tokens() -> None:
    from halia.providers.openai_compat import _parse_usage

    # OpenAI / mimo style: prompt_tokens_details.cached_tokens
    u = _parse_usage({
        "prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105,
        "prompt_tokens_details": {"cached_tokens": 80},
    })
    assert u.prompt_tokens == 100 and u.cached_tokens == 80
    # DeepSeek style: top-level prompt_cache_hit_tokens
    u2 = _parse_usage({"prompt_tokens": 100, "prompt_cache_hit_tokens": 60})
    assert u2.cached_tokens == 60
    # none reported
    assert _parse_usage({"prompt_tokens": 10}).cached_tokens == 0


def test_chat_returns_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer k"
        body = json.loads(request.content)
        assert body["model"] == "m"
        assert body["stream"] is False
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "hi there"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })

    result = _provider(handler).chat([{"role": "user", "content": "hi"}])
    assert result.content == "hi there"
    assert result.tool_calls == []
    assert result.usage.prompt_tokens == 10
    assert result.usage.completion_tokens == 5
    assert result.usage.total_tokens == 15


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
        _provider_retry(handler, empty_retries=0).chat([{"role": "user", "content": "hi"}])


def test_empty_reply_retries_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json={"choices": [{"message": {"content": None}}]})
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    result = _provider_retry(handler, empty_retries=2).chat([{"role": "user", "content": "hi"}])
    assert result.content == "ok"
    assert calls["n"] == 2


def test_empty_reply_gives_up_after_max_attempts() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": None}}]})

    with pytest.raises(ProviderError, match="retried 2"):
        _provider_retry(handler, empty_retries=2).chat([{"role": "user", "content": "hi"}])
    assert calls["n"] == 3  # 1 initial + 2 retries


def test_stream_empty_reply_retries_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            # First stream: usage only, no content/tool-call deltas → empty completion.
            first = {
                "choices": [],
                "usage": {"prompt_tokens": 10, "completion_tokens": 0, "total_tokens": 10},
            }
            return httpx.Response(200, text=_sse(first))
        return httpx.Response(200, text=_sse(
            {"choices": [{"delta": {"content": "recovered"}}]},
            {
                "choices": [],
                "usage": {"prompt_tokens": 10, "completion_tokens": 9, "total_tokens": 19},
            },
        ))

    seen: list[str] = []
    result = _provider_retry(handler, empty_retries=2).chat(
        [{"role": "user", "content": "hi"}], on_delta=seen.append
    )
    assert result.content == "recovered"
    assert calls["n"] == 2


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
        {"choices": [], "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28}},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, text=body)

    seen: list[str] = []
    result = _provider(handler).chat([{"role": "user", "content": "hi"}], on_delta=seen.append)
    assert seen == ["Hel", "lo", " world"]  # streamed piece by piece
    assert result.content == "Hello world"  # and assembled whole
    assert result.tool_calls == []
    assert result.usage.prompt_tokens == 20
    assert result.usage.completion_tokens == 8
    assert result.usage.total_tokens == 28


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


# --- Usage ---


def test_usage_addition() -> None:
    u1 = Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    u2 = Usage(prompt_tokens=20, completion_tokens=8, total_tokens=28)
    combined = u1 + u2
    assert combined.prompt_tokens == 30
    assert combined.completion_tokens == 13
    assert combined.total_tokens == 43


def test_usage_defaults_to_zero() -> None:
    u = Usage()
    assert u.prompt_tokens == 0
    assert u.completion_tokens == 0
    assert u.total_tokens == 0


def test_chat_no_usage_field_returns_zeros() -> None:
    """API responses without a usage field should return Usage(0,0,0)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    result = _provider(handler).chat([{"role": "user", "content": "hi"}])
    assert result.usage == Usage()
    assert result.content == "ok"


def test_stream_http_error_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(ProviderError):
        _provider_retry(handler).chat([{"role": "user", "content": "hi"}], on_delta=lambda s: None)


# --- retry + throttle ---


def test_retries_on_429_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, text="rate limited")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    result = _provider_retry(handler, max_retries=2).chat([{"role": "user", "content": "hi"}])
    assert result.content == "ok"
    assert calls["n"] == 2


def test_retry_gives_up_after_max_attempts() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, text="still down")

    with pytest.raises(ProviderError, match="503"):
        _provider_retry(handler, max_retries=2).chat([{"role": "user", "content": "hi"}])
    assert calls["n"] == 3  # 1 initial + 2 retries


def test_no_retry_on_non_transient_4xx() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, text="bad request")

    with pytest.raises(ProviderError, match="400"):
        _provider_retry(handler, max_retries=3).chat([{"role": "user", "content": "hi"}])
    assert calls["n"] == 1


def test_stream_retries_429_before_first_byte() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, text="rate limited")
        return httpx.Response(200, text=_sse({"choices": [{"delta": {"content": "ok"}}]}))

    seen: list[str] = []
    provider = _provider_retry(handler, max_retries=2)
    result = provider.chat([{"role": "user", "content": "hi"}], on_delta=seen.append)
    assert result.content == "ok"
    assert seen == ["ok"]
    assert calls["n"] == 2


def test_max_rpm_sets_min_interval() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    fast = OpenAICompatProvider(
        base_url="https://x/v1", api_key="k", model="m", client=client, max_rpm=60
    )
    assert fast._min_interval == 1.0
    off = OpenAICompatProvider(
        base_url="https://x/v1", api_key="k", model="m", client=client, max_rpm=0
    )
    assert off._min_interval == 0.0
