"""Tests for the fetch_url skill (mocked HTTP — no network)."""

import httpx
import pytest

from halia.skills.web import FetchUrl, fetch_url_text


def _fetch(handler: object) -> FetchUrl:
    client = httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    return FetchUrl(client=client)


def test_fetch_url_strips_html() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        html = "<html><body><h1>Hi</h1><script>x=1</script><p>World</p></body></html>"
        return httpx.Response(200, text=html)

    out = _fetch(handler).run({"url": "https://example.com"})
    assert "Hi" in out
    assert "World" in out
    assert "x=1" not in out  # <script> contents stripped


def test_fetch_url_non_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="nope")

    out = _fetch(handler).run({"url": "https://example.com"})
    assert "404" in out


def test_fetch_url_requires_http_scheme() -> None:
    out = FetchUrl().run({"url": "ftp://example.com"})
    assert "http" in out.lower()


def test_fetch_url_sends_browser_user_agent() -> None:
    # A read tool must send a real browser UA so sites that block bot UAs (Cloudflare etc.)
    # don't 403 it — its whole job is reading human-facing pages.
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers.get("user-agent", "")
        return httpx.Response(200, text="<p>ok</p>")

    _fetch(handler).run({"url": "https://example.com"})
    assert seen["ua"].startswith("Mozilla/5.0")
    assert "Chrome" in seen["ua"]
    assert "halia" not in seen["ua"].lower()  # no longer the fragile halia/0.1 UA


def test_fetch_url_requires_url() -> None:
    assert "required" in FetchUrl().run({})


def test_fetch_url_is_safe() -> None:
    assert FetchUrl().dangerous is False


def test_fetch_url_text_extracts_and_raises_on_non_200() -> None:
    def ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<h1>Title</h1><p>Body text</p>")

    client = httpx.Client(transport=httpx.MockTransport(ok))  # type: ignore[arg-type]
    text = fetch_url_text("https://example.com", client=client)
    assert "Title" in text and "Body text" in text

    def bad(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client2 = httpx.Client(transport=httpx.MockTransport(bad))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="HTTP 500"):
        fetch_url_text("https://example.com", client=client2)


def test_fetch_url_text_egress_floor_blocks_metadata_ip() -> None:
    from halia.permissions.network import EgressDenied

    # literal link-local IP (cloud metadata) — resolves to itself, blocked pre-network
    with pytest.raises(EgressDenied):
        fetch_url_text("http://169.254.169.254/latest/meta-data/")
