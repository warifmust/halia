"""OpenAI-compatible chat provider (OpenAI, DeepSeek, OpenRouter, …).

A thin client over httpx — no litellm. Auth defaults to `Authorization: Bearer
<key>`, which covers the launch providers; other schemes (MiMo's `api-key`,
Anthropic's native API) get their own provider later.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from halia.providers.base import ChatResult, DeltaObserver, Message, ProviderError, ToolCall

# Read timeout per request (or per streamed chunk). Generous by default for heavy reasoning
# + large outputs; override with HALIA_TIMEOUT (seconds).
try:
    _DEFAULT_TIMEOUT = float(os.environ.get("HALIA_TIMEOUT", "180"))
except ValueError:
    _DEFAULT_TIMEOUT = 180.0


class OpenAICompatProvider:
    """Calls `{base_url}/chat/completions` and returns a `ChatResult`."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = _DEFAULT_TIMEOUT,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        # An injectable client keeps this testable (MockTransport) without network.
        self._client = client if client is not None else httpx.Client(timeout=timeout)

    def _endpoint(self) -> tuple[str, dict[str, str]]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return f"{self._base_url}/chat/completions", headers

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        on_delta: DeltaObserver | None = None,
    ) -> ChatResult:
        if on_delta is not None:
            return self._chat_stream(messages, tools, on_delta)
        return self._chat_once(messages, tools)

    def _chat_once(
        self, messages: list[Message], tools: list[dict[str, Any]] | None
    ) -> ChatResult:
        url, headers = self._endpoint()
        payload: dict[str, Any] = {"model": self._model, "messages": messages, "stream": False}
        if tools:
            payload["tools"] = tools

        try:
            resp = self._client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError(f"request to {url} failed: {exc}") from exc

        if resp.status_code != 200:
            raise ProviderError(f"HTTP {resp.status_code} from {url}: {resp.text}")

        data = resp.json()
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"unexpected response shape: {data!r}") from exc

        content = message.get("content")
        tool_calls = _parse_tool_calls(message.get("tool_calls"))

        # A reply with neither content nor tool calls is a failure (e.g. a reasoning
        # model that spent its whole budget before answering — the agenta lesson).
        if content is None and not tool_calls:
            raise ProviderError(f"model returned no content and no tool calls: {message!r}")

        return ChatResult(content=content, tool_calls=tool_calls)

    def _chat_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        on_delta: DeltaObserver,
    ) -> ChatResult:
        """Stream Server-Sent-Events, emitting content deltas and assembling tool calls."""
        url, headers = self._endpoint()
        payload: dict[str, Any] = {"model": self._model, "messages": messages, "stream": True}
        if tools:
            payload["tools"] = tools

        content_parts: list[str] = []
        # tool-call deltas arrive fragmented, keyed by index → accumulate id/name/arguments.
        acc: dict[int, dict[str, str]] = {}
        try:
            with self._client.stream("POST", url, json=payload, headers=headers) as resp:
                if resp.status_code != 200:
                    body = resp.read().decode("utf-8", "replace")
                    raise ProviderError(f"HTTP {resp.status_code} from {url}: {body}")
                for line in resp.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
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
            raise ProviderError("stream returned no content and no tool calls")
        return ChatResult(content=content, tool_calls=tool_calls)


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
