"""Tests for the fetch_url skill (mocked HTTP — no network)."""

import httpx

from halia.skills.web import FetchUrl


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


def test_fetch_url_requires_url() -> None:
    assert "required" in FetchUrl().run({})


def test_fetch_url_is_safe() -> None:
    assert FetchUrl().dangerous is False
