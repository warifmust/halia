"""HTTP request skill — call an API endpoint (any method) for testing / integration.

The general HTTP primitive: send a request (GET/POST/PUT/PATCH/DELETE/HEAD/OPTIONS)
with headers and a body, and report the response status, timing, content-type and
body. Unlike `fetch_url` (a read-only page *reader*), this can MUTATE remote state
(POST/PUT/DELETE) → it is approval-gated (`dangerous=True`). The egress floor (SSRF)
applies to every request, same as the other web skills.

Trust note: the HTTP STATUS CODE is the grounded fact a test asserts on — reported
exactly, never guessed. Request headers (which may carry auth tokens) are NEVER
echoed back in the observation, so secrets don't leak into logs/audit.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from halia.permissions.network import EgressDenied, check_egress

_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
_DEFAULT_MAX_CHARS = 5000
_TIMEOUT = 20.0
# Automatic retry for TRANSIENT failures — only for idempotent methods. A connection error
# on a POST/PUT/PATCH/DELETE may mean the request WAS delivered and only the response was
# lost, so retrying could double-submit; those methods are never auto-retried.
_IDEMPOTENT = frozenset({"GET", "HEAD", "OPTIONS"})
_RETRY_STATUSES = frozenset({429, 502, 503, 504})
_MAX_RETRIES = 1  # one extra attempt (2 total)
_RETRY_BACKOFF = 0.5  # seconds; a Retry-After header (429/503) takes precedence
# Content-types we render as text; everything else is reported as a byte count.
_TEXTUAL = (
    "text/",
    "application/json",
    "application/xml",
    "application/xhtml",
    "application/javascript",
    "+json",
    "+xml",
)


def _is_textual(content_type: str) -> bool:
    ct = content_type.lower()
    return any(marker in ct for marker in _TEXTUAL)


def _retry_after(resp: httpx.Response) -> float | None:
    """Honour a numeric `Retry-After` header (seconds), capped so we never stall a run."""
    raw = resp.headers.get("retry-after", "").strip()
    if not raw:
        return None
    try:
        return min(max(float(raw), 0.0), 10.0)  # ignore HTTP-date form; cap at 10s
    except ValueError:
        return None


class HttpRequest:
    name = "http_request"
    description = (
        "Make an HTTP request to an API endpoint; returns the response status, timing, "
        "content-type and body. Methods GET/POST/PUT/PATCH/DELETE/HEAD/OPTIONS, with custom "
        "headers and a body (raw string or JSON). The status code is the grounded result to "
        "assert on. Mutating requests are approval-gated."
    )
    dangerous = True
    untrusted = True
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "url": {"type": "string", "description": "The http(s) endpoint URL."},
            "method": {
                "type": "string",
                "enum": list(_METHODS),
                "description": "HTTP method (default GET).",
            },
            "headers": {
                "type": "object",
                "description": "Request headers as a name→value map (e.g. Authorization).",
                "additionalProperties": {"type": "string"},
            },
            "json": {
                "description": (
                    "A JSON body — object or array; sets Content-Type: application/json. "
                    "Takes precedence over `body`."
                ),
            },
            "body": {
                "type": "string",
                "description": "A raw request body string (ignored if `json` is given).",
            },
            "follow_redirects": {
                "type": "boolean",
                "description": "Follow 3xx redirects (default false; a redirect reported as-is).",
            },
            "timeout": {
                "type": "number",
                "description": "Request timeout in seconds (default 20).",
            },
            "max_chars": {
                "type": "integer",
                "description": "Max characters of the response body to return (default 5000).",
            },
        },
        "required": ["url"],
    }

    def __init__(self, client: httpx.Client | None = None) -> None:
        # Injectable for tests; otherwise a client is created per call and closed.
        self._client = client

    def run(self, args: dict[str, Any]) -> str:
        raw = args.get("url")
        if not isinstance(raw, str) or not raw.strip():
            return "error: 'url' is required and must be a non-empty string"
        url = raw.strip()
        if not url.startswith(("http://", "https://")):
            return "error: url must start with http:// or https://"

        method = args.get("method", "GET")
        if not isinstance(method, str) or method.upper() not in _METHODS:
            return f"error: method must be one of {', '.join(_METHODS)}"
        method = method.upper()

        headers = args.get("headers")
        if headers is not None and not (
            isinstance(headers, dict)
            and all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items())
        ):
            return "error: 'headers' must be a map of string names to string values"

        raw_body = args.get("body")
        if raw_body is not None and not isinstance(raw_body, str):
            return "error: 'body' must be a string"

        try:
            check_egress(url)
        except EgressDenied as exc:
            return f"blocked: {exc}"

        timeout = args.get("timeout", _TIMEOUT)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            timeout = _TIMEOUT
        follow = bool(args.get("follow_redirects", False))
        max_chars = args.get("max_chars", _DEFAULT_MAX_CHARS)
        if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars <= 0:
            max_chars = _DEFAULT_MAX_CHARS

        request_kwargs: dict[str, Any] = {
            "headers": headers or {},
            "timeout": timeout,
            "follow_redirects": follow,
        }
        json_body = args.get("json")
        if json_body is not None:
            # Models often pass `json` as a STRING (a JSON-encoded blob). Passing that straight
            # to httpx would JSON-encode it AGAIN → a quoted string body, not an object. Parse
            # a string first; only a genuine object/array/scalar is sent as-is.
            if isinstance(json_body, str):
                try:
                    json_body = json.loads(json_body)
                except json.JSONDecodeError as exc:
                    return f"error: 'json' is a string but not valid JSON ({exc}); pass an object"
            request_kwargs["json"] = json_body
        elif isinstance(raw_body, str):
            request_kwargs["content"] = raw_body.encode("utf-8")

        client = self._client or httpx.Client()
        retryable = method in _IDEMPOTENT
        started = time.perf_counter()
        try:
            attempt = 0
            while True:
                try:
                    resp = client.request(method, url, **request_kwargs)
                except httpx.HTTPError as exc:
                    # Transport-level blip (timeout, connection reset). Retry idempotent only.
                    if retryable and attempt < _MAX_RETRIES:
                        attempt += 1
                        time.sleep(_RETRY_BACKOFF)
                        continue
                    return f"error: {method} {url} failed: {exc}"
                # Transient server-side status (429/503/…): one more try for idempotent methods.
                if resp.status_code in _RETRY_STATUSES and retryable and attempt < _MAX_RETRIES:
                    attempt += 1
                    time.sleep(_retry_after(resp) or _RETRY_BACKOFF)
                    continue
                break
        finally:
            if self._client is None:
                client.close()
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        return self._format(method, url, resp, elapsed_ms, max_chars)

    @staticmethod
    def _format(
        method: str, url: str, resp: httpx.Response, elapsed_ms: float, max_chars: int
    ) -> str:
        reason = f" {resp.reason_phrase}" if resp.reason_phrase else ""
        lines = [f"{method} {url} → {resp.status_code}{reason} ({elapsed_ms:.0f} ms)"]

        content_type = resp.headers.get("content-type", "")
        if content_type:
            lines.append(f"content-type: {content_type}")
        location = resp.headers.get("location")
        if location:
            lines.append(f"location: {location}")

        if method == "HEAD":
            lines.append("(HEAD — no body)")
        elif _is_textual(content_type) or not content_type:
            text = resp.text
            n = len(text)
            if not text:
                lines.append("body: (empty)")
            else:
                if n > max_chars:
                    text = text[:max_chars] + "… [truncated]"
                lines.append(f"body ({n} chars):\n{text}")
        else:
            lines.append(f"body: <non-text {content_type}, {len(resp.content)} bytes>")

        return "\n".join(lines)
