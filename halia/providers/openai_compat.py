"""OpenAI-compatible chat provider (OpenAI, DeepSeek, OpenRouter, …).

A thin client over httpx — no litellm. Auth defaults to `Authorization: Bearer
<key>`, which covers the launch providers; other schemes (MiMo's `api-key`,
Anthropic's native API) get their own provider later.
"""

from __future__ import annotations

from typing import Any

import httpx

from halia.providers.base import ChatResult, Message, ProviderError, ToolCall


class OpenAICompatProvider:
    """Calls `{base_url}/chat/completions` and returns a `ChatResult`."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        # An injectable client keeps this testable (MockTransport) without network.
        self._client = client if client is not None else httpx.Client(timeout=timeout)

    def chat(
        self, messages: list[Message], tools: list[dict[str, Any]] | None = None
    ) -> ChatResult:
        url = f"{self._base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
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
