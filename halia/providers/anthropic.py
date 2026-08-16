"""Anthropic-native chat provider (Claude models via the Messages API).

A thin client over httpx for Anthropic's native API. The API shape differs from
OpenAI's in several ways (system is a top-level field, tool format is different,
tool results use content-block syntax), so this is a separate provider rather
than shoehorning Anthropic into the OpenAI-compat adapter.
"""

from __future__ import annotations

import json
import os
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

_ANTHROPIC_VERSION = "2023-06-01"

try:
    _DEFAULT_TIMEOUT = float(os.environ.get("HALIA_TIMEOUT", "180"))
except ValueError:
    _DEFAULT_TIMEOUT = 180.0

# Anthropic requires an explicit max_tokens on every request (unlike OpenAI). Default is
# generous for report/QA-doc generation; override with HALIA_MAX_TOKENS.
try:
    _DEFAULT_MAX_TOKENS = int(os.environ.get("HALIA_MAX_TOKENS", "8192"))
except ValueError:
    _DEFAULT_MAX_TOKENS = 8192


class AnthropicProvider:
    """Calls the Anthropic Messages API and returns a `ChatResult`."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = _DEFAULT_TIMEOUT,
        client: httpx.Client | None = None,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._client = client if client is not None else httpx.Client(timeout=timeout)

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        on_delta: DeltaObserver | None = None,
    ) -> ChatResult:
        system, converted = _convert_messages(messages)
        anthropic_tools = _convert_tools(tools) if tools else None

        if on_delta is not None:
            return self._chat_stream(system, converted, anthropic_tools, on_delta)
        return self._chat_once(system, converted, anthropic_tools)

    def _chat_once(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> ChatResult:
        url = f"{self._base_url}/messages"
        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": messages,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = tools

        try:
            resp = self._client.post(url, json=payload, headers=self._headers())
        except httpx.HTTPError as exc:
            raise ProviderError(f"request to {url} failed: {exc}") from exc

        if resp.status_code != 200:
            raise ProviderError(f"HTTP {resp.status_code} from {url}: {resp.text}")

        return _parse_response(resp.json())

    def _chat_stream(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        on_delta: DeltaObserver,
    ) -> ChatResult:
        url = f"{self._base_url}/messages"
        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": messages,
            "stream": True,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = tools

        content_parts: list[str] = []
        # Accumulate tool-use blocks keyed by index (they arrive as content-block deltas).
        tool_acc: dict[int, dict[str, Any]] = {}
        input_tokens = 0
        output_tokens = 0
        cache_read = 0
        cache_write = 0

        try:
            with self._client.stream("POST", url, json=payload, headers=self._headers()) as resp:
                if resp.status_code != 200:
                    body = resp.read().decode("utf-8", "replace")
                    raise ProviderError(f"HTTP {resp.status_code} from {url}: {body}")
                for line in resp.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    ev_type = event.get("type", "")
                    if ev_type == "message_start":
                        # input_tokens (+ cache reads/writes) arrive in the first event.
                        msg_usage = event.get("message", {}).get("usage", {})
                        input_tokens = int(msg_usage.get("input_tokens", 0) or 0)
                        cache_read = int(msg_usage.get("cache_read_input_tokens", 0) or 0)
                        cache_write = int(msg_usage.get("cache_creation_input_tokens", 0) or 0)
                    elif ev_type == "message_delta":
                        # output_tokens arrives in the final delta event.
                        delta_usage = event.get("usage", {})
                        output_tokens = int(delta_usage.get("output_tokens", 0) or 0)
                    elif ev_type == "content_block_delta":
                        delta = event.get("delta", {})
                        if delta.get("type") == "text_delta":
                            piece = delta.get("text", "")
                            if piece:
                                content_parts.append(piece)
                                on_delta(piece)
                        elif delta.get("type") == "input_json_delta":
                            idx = event.get("index", 0)
                            entry = tool_acc.setdefault(idx, {"id": "", "name": "", "input": ""})
                            entry["input"] += delta.get("partial_json", "")
                    elif ev_type == "content_block_start":
                        block = event.get("content_block", {})
                        if block.get("type") == "tool_use":
                            idx = event.get("index", 0)
                            entry = tool_acc.setdefault(idx, {"id": "", "name": "", "input": ""})
                            entry["id"] = block.get("id", "")
                            entry["name"] = block.get("name", "")
        except httpx.HTTPError as exc:
            raise ProviderError(f"request to {url} failed: {exc}") from exc

        content = "".join(content_parts) or None
        tool_calls = [
            ToolCall(
                id=e["id"],
                name=e["name"],
                arguments=e["input"],
            )
            for _, e in sorted(tool_acc.items())
        ]
        if content is None and not tool_calls:
            raise ProviderError("stream returned no content and no tool calls")
        prompt = input_tokens + cache_read + cache_write
        usage = Usage(
            prompt_tokens=prompt,
            completion_tokens=output_tokens,
            total_tokens=prompt + output_tokens,
            cached_tokens=cache_read,
        )
        return ChatResult(content=content, tool_calls=tool_calls, usage=usage)


# ── Message / tool conversion ──────────────────────────────────────────────────


def _convert_messages(
    messages: list[Message],
) -> tuple[str, list[dict[str, Any]]]:
    """Split the system prompt off and convert the rest to Anthropic format.

    Anthropic keeps `system` as a top-level field (not a message). The remaining
    messages are mapped: user/assistant pass through; tool-result messages become
    user messages with tool_result content blocks. Assistant messages that carry
    tool_calls are converted to have tool_use content blocks.
    """
    system_parts: list[str] = []
    converted: list[dict[str, Any]] = []

    i = 0
    while i < len(messages) and messages[i].get("role") == "system":
        content = messages[i].get("content", "")
        if isinstance(content, str) and content.strip():
            system_parts.append(content)
        i += 1

    for msg in messages[i:]:
        role = msg.get("role", "")
        content = msg.get("content")

        if role == "tool":
            # Tool result → user message with tool_result content block.
            tool_id = msg.get("tool_call_id", "")
            text = content if isinstance(content, str) else str(content)
            converted.append({
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": text}],
            })
        elif role == "assistant" and msg.get("tool_calls"):
            # Assistant turn with tool calls → text + tool_use blocks.
            blocks: list[dict[str, Any]] = []
            if isinstance(content, str) and content.strip():
                blocks.append({"type": "text", "text": content})
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                try:
                    inp = json.loads(fn.get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    inp = {}
                blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id", ""),
                    "name": fn.get("name", ""),
                    "input": inp,
                })
            converted.append({"role": "assistant", "content": blocks})
        elif role in ("user", "assistant"):
            # Handle list content (images) and string content
            if isinstance(content, list):
                # Convert OpenAI image_url format to Anthropic format
                image_blocks: list[dict[str, Any]] = []
                for item in content:
                    if isinstance(item, dict):
                        if item.get("type") == "image_url":
                            url = item.get("image_url", {}).get("url", "")
                            if url.startswith("data:"):
                                # Extract base64 data from data URL
                                # Format: data:image/png;base64,<data>
                                parts = url.split(",", 1)
                                if len(parts) == 2:
                                    media_type = parts[0].split(":")[1].split(";")[0]
                                    image_blocks.append({
                                        "type": "image",
                                        "source": {
                                            "type": "base64",
                                            "media_type": media_type,
                                            "data": parts[1],
                                        },
                                    })
                            else:
                                # URL-based image
                                image_blocks.append({
                                    "type": "image",
                                    "source": {
                                        "type": "url",
                                        "url": url,
                                    },
                                })
                        elif item.get("type") == "text":
                            image_blocks.append({
                                "type": "text",
                                "text": item.get("text", ""),
                            })
                        else:
                            # Pass through other block types
                            image_blocks.append(item)
                converted.append({"role": role, "content": image_blocks})
            else:
                text = content if isinstance(content, str) else ""
                converted.append({"role": role, "content": text})
        # Ignore unknown roles (Anthropic is strict).

    return "\n\n".join(system_parts), converted


def _convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert OpenAI-style tool schemas to Anthropic format."""
    result: list[dict[str, Any]] = []
    for tool in tools:
        fn = tool.get("function", {})
        result.append({
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
        })
    return result


def _parse_response(data: dict[str, Any]) -> ChatResult:
    """Parse an Anthropic Messages API response into a `ChatResult`."""
    content_blocks = data.get("content") or []
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []

    for block in content_blocks:
        if block.get("type") == "text":
            text_parts.append(block.get("text", ""))
        elif block.get("type") == "tool_use":
            inp = block.get("input", {})
            tool_calls.append(ToolCall(
                id=block.get("id", ""),
                name=block.get("name", ""),
                arguments=json.dumps(inp) if isinstance(inp, dict) else str(inp),
            ))

    content = "\n".join(text_parts) or None
    if content is None and not tool_calls:
        raise ProviderError(f"model returned no content and no tool calls: {data!r}")

    # Anthropic reports input/output separately, with cache reads/writes as their own fields.
    raw_usage = data.get("usage") or {}
    input_tokens = int(raw_usage.get("input_tokens", 0) or 0)
    output_tokens = int(raw_usage.get("output_tokens", 0) or 0)
    cache_read = int(raw_usage.get("cache_read_input_tokens", 0) or 0)
    cache_write = int(raw_usage.get("cache_creation_input_tokens", 0) or 0)
    prompt = input_tokens + cache_read + cache_write
    usage = Usage(
        prompt_tokens=prompt,
        completion_tokens=output_tokens,
        total_tokens=prompt + output_tokens,
        cached_tokens=cache_read,
    )
    return ChatResult(content=content, tool_calls=tool_calls, usage=usage)
