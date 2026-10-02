"""OpenAI-compatible chat provider (OpenAI, DeepSeek, OpenRouter, MiMo, …).

A thin client over httpx — no litellm. Auth is configurable: `Bearer` (OpenAI,
DeepSeek, OpenRouter), `api-key` (MiMo), or any custom header. Anthropic's
native API gets its own provider (`providers/anthropic.py`).
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
from typing import Any

import httpx

from halia.providers.base import (
    ChatResult,
    DeltaObserver,
    Message,
    ProviderError,
    ToolCall,
    Usage,
)

# Read timeout per request (or per streamed chunk). None = no timeout (the default).
# Set HALIA_TIMEOUT (seconds) to re-enable a per-request/per-chunk read timeout.
_raw_timeout = os.environ.get("HALIA_TIMEOUT", "").strip()
try:
    _DEFAULT_TIMEOUT: float | None = float(_raw_timeout) if _raw_timeout else None
except ValueError:
    _DEFAULT_TIMEOUT = None

# Absolute cap on a single model generation. 0 = no cap (the default). Set
# HALIA_GENERATION_TIMEOUT (seconds) to cap a single generation.
try:
    _GENERATION_TIMEOUT = float(os.environ.get("HALIA_GENERATION_TIMEOUT", "0"))
except ValueError:
    _GENERATION_TIMEOUT = 0.0

# Transient statuses worth retrying with backoff. 429 (rate limit) and 5xx (server errors)
# usually clear within seconds; other 4xx are deterministic (auth, bad request) and must not
# be retried.
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

# Retry-on-429/5xx knobs: max attempts AFTER the first, base backoff (seconds, exponential),
# and a cap on the per-attempt delay. Override via env for tight loops or generous tiers.
try:
    _DEFAULT_MAX_RETRIES = int(os.environ.get("HALIA_RETRY_MAX", "3"))
except ValueError:
    _DEFAULT_MAX_RETRIES = 3
try:
    _DEFAULT_RETRY_BASE = float(os.environ.get("HALIA_RETRY_BASE", "1.0"))
except ValueError:
    _DEFAULT_RETRY_BASE = 1.0
try:
    _DEFAULT_RETRY_CAP = float(os.environ.get("HALIA_RETRY_CAP", "30"))
except ValueError:
    _DEFAULT_RETRY_CAP = 30.0

# Opt-in client-side throttle: max requests per minute (0 = disabled). HALIA_MAX_RPM=60
# enforces a 1s minimum gap between model calls, keeping a tight loop under a provider's
# RPM tier instead of tripping 429 and relying on retries.
try:
    _DEFAULT_MAX_RPM = int(os.environ.get("HALIA_MAX_RPM", "0"))
except ValueError:
    _DEFAULT_MAX_RPM = 0

# Optional cap on a reasoning model's thinking budget, passed through verbatim as
# `reasoning_effort` (e.g. "low"/"medium"/"high") when the provider supports it.
# Empty = not sent, so the provider's own default applies. HALIA_REASONING_EFFORT.
_DEFAULT_REASONING_EFFORT = os.environ.get("HALIA_REASONING_EFFORT", "").strip() or None

# Retry a 200 response that carried no content AND no tool calls ("empty completion").
# Common on free/flaky endpoints (truncated stream, soft rate-limit). 0 = hard error.
# HALIA_EMPTY_RETRIES.
try:
    _DEFAULT_EMPTY_RETRIES = int(os.environ.get("HALIA_EMPTY_RETRIES", "2"))
except ValueError:
    _DEFAULT_EMPTY_RETRIES = 2


class _RetryableResponse(Exception):
    """A 429/5xx received BEFORE any streamed token — safe to retry the whole request."""

    def __init__(self, message: str, response: httpx.Response) -> None:
        super().__init__(message)
        self.message = message
        self.response = response


class _EmptyCompletion(Exception):
    """A 200 response with no content and no tool calls — retryable (flaky endpoints)."""


class OpenAICompatProvider:
    """Calls `{base_url}/chat/completions` and returns a `ChatResult`.

    `auth_header` controls how the API key is sent: "Bearer" → `Authorization: Bearer
    <key>`, "api-key" → `api-key: <key>`, etc. Defaults to "Bearer".
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float | None = _DEFAULT_TIMEOUT,
        generation_timeout: float = _GENERATION_TIMEOUT,
        client: httpx.Client | None = None,
        auth_header: str = "Bearer",
        max_retries: int = _DEFAULT_MAX_RETRIES,
        retry_base: float = _DEFAULT_RETRY_BASE,
        retry_cap: float = _DEFAULT_RETRY_CAP,
        max_rpm: int = _DEFAULT_MAX_RPM,
        reasoning_effort: str | None = _DEFAULT_REASONING_EFFORT,
        empty_retries: int = _DEFAULT_EMPTY_RETRIES,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._generation_timeout = generation_timeout
        self._auth_header = auth_header
        self._max_retries = max_retries
        self._retry_base = retry_base
        self._retry_cap = retry_cap
        self._reasoning_effort = reasoning_effort
        self._empty_retries = empty_retries
        # Optional client-side RPM throttle (0 = disabled).
        self._min_interval = 60.0 / max_rpm if max_rpm > 0 else 0.0
        self._last_request_ts = 0.0
        self._throttle_lock = threading.Lock()
        # An injectable client keeps this testable (MockTransport) without network.
        self._client = client if client is not None else httpx.Client(timeout=timeout)

    def _endpoint(self) -> tuple[str, dict[str, str]]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            if self._auth_header == "Bearer":
                headers["Authorization"] = f"Bearer {self._api_key}"
            else:
                headers[self._auth_header] = self._api_key
        return f"{self._base_url}/chat/completions", headers

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        on_delta: DeltaObserver | None = None,
    ) -> ChatResult:
        self._throttle()
        if on_delta is not None:
            return self._chat_stream(messages, tools, on_delta)
        return self._chat_once(messages, tools)

    def _throttle(self) -> None:
        """Enforce a minimum gap between requests when HALIA_MAX_RPM is set."""
        if self._min_interval <= 0:
            return
        with self._throttle_lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last_request_ts)
            if wait > 0:
                time.sleep(wait)
            self._last_request_ts = time.monotonic()

    def _backoff_delay(self, resp: httpx.Response, attempt: int) -> float:
        """Delay before retry attempt `attempt`: honor Retry-After, else exponential + jitter."""
        retry_after = resp.headers.get("retry-after")
        if retry_after:
            try:
                seconds = float(retry_after)
            except ValueError:
                seconds = -1.0
            if seconds > 0:
                return seconds if seconds < self._retry_cap else self._retry_cap
        base = self._retry_base * (2 ** (attempt - 1))
        jitter = 1.0 + random.random() * 0.5
        delay = base * jitter
        return delay if delay < self._retry_cap else self._retry_cap

    def _empty_backoff(self, attempt: int) -> float:
        """Delay before retrying an empty completion (no Retry-After header to honor)."""
        delay = self._retry_base * (2 ** (attempt - 1))
        return delay if delay < self._retry_cap else self._retry_cap

    def _chat_once(
        self, messages: list[Message], tools: list[dict[str, Any]] | None
    ) -> ChatResult:
        url, headers = self._endpoint()
        payload: dict[str, Any] = {"model": self._model, "messages": messages, "stream": False}
        if tools:
            payload["tools"] = tools
        if self._reasoning_effort:
            payload["reasoning_effort"] = self._reasoning_effort

        attempt = 0
        empty_tries = 0
        while True:
            try:
                resp = self._client.post(url, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                raise ProviderError(f"request to {url} failed: {exc}") from exc
            if resp.status_code != 200:
                if resp.status_code in _RETRYABLE_STATUSES and attempt < self._max_retries:
                    attempt += 1
                    time.sleep(self._backoff_delay(resp, attempt))
                    continue
                raise ProviderError(f"HTTP {resp.status_code} from {url}: {resp.text}")

            data = resp.json()
            try:
                message = data["choices"][0]["message"]
            except (KeyError, IndexError, TypeError) as exc:
                raise ProviderError(f"unexpected response shape: {data!r}") from exc

            content = message.get("content")
            tool_calls = _parse_tool_calls(message.get("tool_calls"))

            # A reply with neither content nor tool calls is a failure (e.g. a reasoning
            # model that spent its whole budget before answering, or a flaky free endpoint
            # returning an empty body). Retry a few times before giving up.
            if content is None and not tool_calls:
                if empty_tries < self._empty_retries:
                    empty_tries += 1
                    time.sleep(self._empty_backoff(empty_tries))
                    continue
                raise ProviderError(
                    "model returned no content and no tool calls "
                    f"(endpoint may be throttled/truncated) — retried "
                    f"{self._empty_retries}× and gave up"
                )

            usage = _parse_usage(data.get("usage"))
            return ChatResult(content=content, tool_calls=tool_calls, usage=usage)

    def _chat_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        on_delta: DeltaObserver,
    ) -> ChatResult:
        """Stream Server-Sent-Events, retrying on a pre-first-byte 429/5xx."""
        url, headers = self._endpoint()
        payload: dict[str, Any] = {"model": self._model, "messages": messages, "stream": True}
        if tools:
            payload["tools"] = tools
        # Request usage in the final streaming chunk (OpenAI, DeepSeek, OpenRouter support this).
        payload["stream_options"] = {"include_usage": True}
        if self._reasoning_effort:
            payload["reasoning_effort"] = self._reasoning_effort

        attempt = 0
        empty_tries = 0
        while True:
            try:
                return self._chat_stream_attempt(url, payload, headers, on_delta)
            except _RetryableResponse as exc:
                if attempt >= self._max_retries:
                    raise ProviderError(exc.message) from exc
                attempt += 1
                time.sleep(self._backoff_delay(exc.response, attempt))
            except _EmptyCompletion:
                if empty_tries >= self._empty_retries:
                    raise ProviderError(
                        "model stream returned no content and no tool calls "
                        f"(endpoint may be throttled/truncated) — retried "
                        f"{self._empty_retries}× and gave up"
                    ) from None
                empty_tries += 1
                time.sleep(self._empty_backoff(empty_tries))

    def _chat_stream_attempt(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        on_delta: DeltaObserver,
    ) -> ChatResult:
        """One streaming request; raises `_RetryableResponse` for a pre-first-byte 429/5xx."""
        content_parts: list[str] = []
        # tool-call deltas arrive fragmented, keyed by index → accumulate id/name/arguments.
        acc: dict[int, dict[str, str]] = {}
        stream_usage: Usage = Usage()
        started = time.perf_counter()
        try:
            with self._client.stream("POST", url, json=payload, headers=headers) as resp:
                if resp.status_code != 200:
                    body = resp.read().decode("utf-8", "replace")
                    if resp.status_code in _RETRYABLE_STATUSES:
                        raise _RetryableResponse(
                            f"HTTP {resp.status_code} from {url}: {body}", resp
                        )
                    raise ProviderError(f"HTTP {resp.status_code} from {url}: {body}")
                for line in resp.iter_lines():
                    if self._generation_timeout > 0 and \
                            time.perf_counter() - started > self._generation_timeout:
                        raise ProviderError(
                            f"generation timed out after {self._generation_timeout:.0f}s"
                        )
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    # Usage arrives in the final chunk (choices may be empty there).
                    usage_raw = chunk.get("usage")
                    if usage_raw:
                        stream_usage = _parse_usage(usage_raw)
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    piece = delta.get("content")
                    if piece:
                        content_parts.append(piece)
                        on_delta(piece)
                    for tc in delta.get("tool_calls") or []:
                        entry = acc.setdefault(
                            tc.get("index", 0), {"id": "", "name": "", "arguments": ""}
                        )
                        if tc.get("id"):
                            entry["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            entry["name"] = fn["name"]
                        if fn.get("arguments"):
                            entry["arguments"] += fn["arguments"]
        except httpx.HTTPError as exc:
            raise ProviderError(f"request to {url} failed: {exc}") from exc

        content = "".join(content_parts) or None
        tool_calls = [
            ToolCall(id=e["id"], name=e["name"], arguments=e["arguments"])
            for _, e in sorted(acc.items())
        ]
        if content is None and not tool_calls:
            raise _EmptyCompletion()
        return ChatResult(content=content, tool_calls=tool_calls, usage=stream_usage)


def _parse_usage(raw: Any) -> Usage:
    """Parse OpenAI-style usage, including cached prompt tokens where the provider reports them.

    Cached tokens live in `prompt_tokens_details.cached_tokens` (OpenAI, mimo) or, on DeepSeek,
    the top-level `prompt_cache_hit_tokens`. They're still counted in prompt_tokens — just billed
    at the cheaper cache rate.
    """
    if not raw or not isinstance(raw, dict):
        return Usage()
    details = raw.get("prompt_tokens_details")
    cached = int(details.get("cached_tokens", 0) or 0) if isinstance(details, dict) else 0
    if not cached:
        cached = int(raw.get("prompt_cache_hit_tokens", 0) or 0)
    return Usage(
        prompt_tokens=int(raw.get("prompt_tokens", 0) or 0),
        completion_tokens=int(raw.get("completion_tokens", 0) or 0),
        total_tokens=int(raw.get("total_tokens", 0) or 0),
        cached_tokens=cached,
    )


def _parse_tool_calls(raw: Any) -> list[ToolCall]:
    if not raw:
        return []
    calls: list[ToolCall] = []
    for tc in raw:
        try:
            calls.append(
                ToolCall(
                    id=tc["id"],
                    name=tc["function"]["name"],
                    arguments=tc["function"].get("arguments", "") or "",
                )
            )
        except (KeyError, TypeError) as exc:
            raise ProviderError(f"malformed tool_call: {tc!r}") from exc
    return calls
