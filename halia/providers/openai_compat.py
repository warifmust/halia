"""OpenAI-compatible chat provider (OpenAI, DeepSeek, OpenRouter, …).

A ~50-line client over httpx — no litellm. Auth defaults to `Authorization:
Bearer <key>`, which covers the launch providers; other schemes (e.g. MiMo's
`api-key` header, Anthropic's native API) get their own provider later.
"""

from __future__ import annotations

import httpx

from halia.providers.base import Message, ProviderError


class OpenAICompatProvider:
    """Calls `{base_url}/chat/completions` and returns the reply text."""

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

    def chat(self, messages: list[Message]) -> str:
        url = f"{self._base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = {"model": self._model, "messages": messages, "stream": False}

        try:
            resp = self._client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError(f"request to {url} failed: {exc}") from exc

        if resp.status_code != 200:
            raise ProviderError(f"HTTP {resp.status_code} from {url}: {resp.text}")

        data = resp.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"unexpected response shape: {data!r}") from exc

        if not isinstance(content, str):
            raise ProviderError(f"non-string content in response: {content!r}")
        return content.strip()
