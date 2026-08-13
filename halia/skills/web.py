"""Web research skills — search for sources, and fetch a page's readable text.

Safe (read-only). Uses the existing httpx dependency + stdlib parsing, so no new
dependency. Network egress is a future leash concern (data-stays-local); for now
these GET public pages only.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from halia.permissions.network import EgressDenied, check_egress

_DEFAULT_MAX_CHARS = 5000
_TIMEOUT = 20.0
# A real browser User-Agent so read tools (fetch_url / web_search) reliably reach pages that
# block non-browser clients (Cloudflare etc. flag bot UAs like `python-httpx`). This is a READ
# tool sending a polite, common UA — distinct from http_request, which sends only the tester's
# headers verbatim (fidelity for API testing).
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


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


def fetch_url_raw(url: str, client: httpx.Client | None = None) -> tuple[str, str]:
    """Fetch a URL and return (content_type, raw body) — no HTML stripping. Egress-floored.

    The single low-level fetch: fetch_url_text strips this for readable text, and the
    OpenAPI resolver inspects the raw body to find a spec URL. Raises EgressDenied,
    ValueError (bad scheme or non-200 status), or httpx.HTTPError.
    """
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("url must start with http:// or https://")
    check_egress(url)
    owns = client is None
    http = client or httpx.Client(timeout=_TIMEOUT, follow_redirects=True)
    try:
        resp = http.get(url, headers={"User-Agent": _BROWSER_UA})
    finally:
        if owns:
            http.close()
    if resp.status_code != 200:
        raise ValueError(f"HTTP {resp.status_code} from {url}")
    return resp.headers.get("content-type", ""), resp.text


def fetch_url_text(
    url: str, client: httpx.Client | None = None, max_chars: int = _DEFAULT_MAX_CHARS
) -> str:
    """Fetch a URL and return its readable text (HTML/scripts stripped). Egress-floored."""
    _ct, body = fetch_url_raw(url, client=client)
    parser = _TextExtractor()
    parser.feed(body)
    text = parser.text()
    if max_chars and len(text) > max_chars:
        text = text[:max_chars] + "… [truncated]"
    return text


class FetchUrl:
    name = "fetch_url"
    description = (
        "Fetch a web page over HTTP(S) and return its readable text "
        "(HTML tags and scripts stripped, truncated to a safe size)."
    )
    dangerous = False
    untrusted = True
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
            text = fetch_url_text(url, client=self._client, max_chars=max_chars)
        except EgressDenied as exc:
            return f"blocked: {exc}"
        except httpx.HTTPError as exc:
            return f"error fetching {url}: {exc}"
        except ValueError as exc:
            return f"error: {exc}"
        return text or "(no text content)"


_SEARCH_URL = "https://html.duckduckgo.com/html/"
_DEFAULT_MAX_RESULTS = 5
_RESULT_A = re.compile(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
_SNIPPET = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.DOTALL)


def _strip_tags(fragment: str) -> str:
    """Turn an HTML fragment into plain text (drop tags, unescape entities, collapse space)."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", fragment)).split())


def _real_url(href: str) -> str:
    """Resolve a DuckDuckGo `/l/?uddg=…` redirect to the actual target URL."""
    href = html.unescape(href)
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg")
        if target:
            return unquote(target[0])
    return href


def parse_search_results(document: str, max_results: int) -> list[tuple[str, str, str]]:
    """Parse (title, url, snippet) tuples from a DuckDuckGo HTML results page."""
    anchors = _RESULT_A.findall(document)
    snippets = _SNIPPET.findall(document)
    results: list[tuple[str, str, str]] = []
    for i, (href, title_html) in enumerate(anchors[:max_results]):
        title = _strip_tags(title_html)
        snippet = _strip_tags(snippets[i]) if i < len(snippets) else ""
        results.append((title, _real_url(href), snippet))
    return results


class WebSearch:
    name = "web_search"
    description = (
        "Search the web (via DuckDuckGo) and return the top results as title, URL, and "
        "snippet. Use this to DISCOVER sources, then fetch_url to read a promising result."
    )
    dangerous = False
    untrusted = True
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "query": {"type": "string", "description": "What to search for."},
            "max_results": {
                "type": "integer",
                "description": "How many results to return (default 5).",
            },
        },
        "required": ["query"],
    }

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = (
            client if client is not None else httpx.Client(timeout=_TIMEOUT, follow_redirects=True)
        )

    def run(self, args: dict[str, Any]) -> str:
        raw = args.get("query")
        if not isinstance(raw, str) or not raw.strip():
            return "error: 'query' is required and must be a non-empty string"
        query = raw.strip()

        max_results = args.get("max_results", _DEFAULT_MAX_RESULTS)
        if not isinstance(max_results, int) or max_results <= 0:
            max_results = _DEFAULT_MAX_RESULTS

        try:
            resp = self._client.get(
                _SEARCH_URL, params={"q": query}, headers={"User-Agent": _BROWSER_UA}
            )
        except httpx.HTTPError as exc:
            return f"error searching for {query!r}: {exc}"
        if resp.status_code != 200:
            return f"error: HTTP {resp.status_code} from the search endpoint"

        results = parse_search_results(resp.text, max_results)
        if not results:
            return f"no results for {query!r}"
        lines = [f"{len(results)} result(s) for {query!r}:"]
        for i, (title, url, snippet) in enumerate(results, 1):
            lines.append(f"{i}. {title}\n   {url}\n   {snippet}")
        return "\n".join(lines)
