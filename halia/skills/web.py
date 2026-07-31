"""Web research skill — fetch a page and return its readable text.

Safe (read-only). Uses the existing httpx dependency + stdlib html.parser to
strip tags/scripts, so no new dependency. Network egress is a future leash
concern (data-stays-local); for now this GETs public pages only.
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any

import httpx

_DEFAULT_MAX_CHARS = 5000
_TIMEOUT = 20.0


class _TextExtractor(HTMLParser):
    """Collect visible text, skipping <script>/<style> contents."""

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip > 0:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._skip == 0:
            self._chunks.append(data)

    def text(self) -> str:
        return " ".join(" ".join(self._chunks).split())


class FetchUrl:
    name = "fetch_url"
    description = (
        "Fetch a web page over HTTP(S) and return its readable text "
        "(HTML tags and scripts stripped, truncated to a safe size)."
    )
    dangerous = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "url": {"type": "string", "description": "The http(s) URL to fetch."},
            "max_chars": {
                "type": "integer",
                "description": "Max characters of text to return (default 5000).",
            },
        },
        "required": ["url"],
    }

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = (
            client if client is not None else httpx.Client(timeout=_TIMEOUT, follow_redirects=True)
        )

    def run(self, args: dict[str, Any]) -> str:
        raw = args.get("url")
        if not isinstance(raw, str) or not raw.strip():
            return "error: 'url' is required and must be a non-empty string"
        url = raw.strip()
        if not url.startswith(("http://", "https://")):
            return "error: url must start with http:// or https://"

        max_chars = args.get("max_chars", _DEFAULT_MAX_CHARS)
        if not isinstance(max_chars, int) or max_chars <= 0:
            max_chars = _DEFAULT_MAX_CHARS

        try:
            resp = self._client.get(url, headers={"User-Agent": "halia/0.1"})
        except httpx.HTTPError as exc:
            return f"error fetching {url}: {exc}"
        if resp.status_code != 200:
            return f"error: HTTP {resp.status_code} from {url}"

        parser = _TextExtractor()
        parser.feed(resp.text)
        text = parser.text()
        if len(text) > max_chars:
            text = text[:max_chars] + "… [truncated]"
        return text or "(no text content)"
