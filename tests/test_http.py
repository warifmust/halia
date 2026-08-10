"""Tests for the http_request skill (mocked transport — no real network)."""

from typing import Any

import httpx
import pytest

from halia.skills.http import HttpRequest


def _skill(handler: object) -> HttpRequest:
    client = httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    return HttpRequest(client=client)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: Any) -> None:
    # Retry backoff must not actually stall the test suite.
    import halia.skills.http as http_mod

    monkeypatch.setattr(http_mod.time, "sleep", lambda _s: None)


def test_get_reports_status_and_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(200, json={"ok": True})

    out = _skill(handler).run({"url": "https://example.com/api"})
    assert "→ 200" in out
    assert "content-type: application/json" in out
    assert '"ok":true' in out


def test_post_sends_json_body() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["content_type"] = request.headers.get("content-type")
        seen["body"] = request.content.decode()
        return httpx.Response(201, text="created")

    out = _skill(handler).run(
        {
            "url": "https://example.com/auth/login",
            "method": "POST",
            "json": {"email": "a@b.com", "password": "x"},
        }
    )
    assert seen["method"] == "POST"
    assert "application/json" in str(seen["content_type"])
    assert "a@b.com" in str(seen["body"])
    assert "→ 201" in out


def test_raw_body_and_custom_headers() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = request.content.decode()
        return httpx.Response(200, text="ok")

    _skill(handler).run(
        {
            "url": "https://example.com/x",
            "method": "PUT",
            "headers": {"Authorization": "Bearer tok"},
            "body": "raw-payload",
        }
    )
    assert seen["auth"] == "Bearer tok"
    assert seen["body"] == "raw-payload"


def test_auth_header_not_echoed_in_output() -> None:
    # Secrets in request headers must never leak into the observation.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")

    out = _skill(handler).run(
        {"url": "https://example.com/x", "headers": {"Authorization": "Bearer SECRET"}}
    )
    assert "SECRET" not in out


def test_error_status_is_reported_exactly() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    out = _skill(handler).run({"url": "https://example.com/missing"})
    assert "→ 404" in out


def test_redirect_not_followed_by_default() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://example.com/next"})

    out = _skill(handler).run({"url": "https://example.com/old"})
    assert "→ 302" in out
    assert "location: https://example.com/next" in out


def test_head_has_no_body_section() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"})

    out = _skill(handler).run({"url": "https://example.com", "method": "HEAD"})
    assert "(HEAD — no body)" in out


def test_non_text_body_reported_as_bytes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "image/png"}, content=b"\x89PNG\r\n")

    out = _skill(handler).run({"url": "https://example.com/logo.png"})
    assert "non-text image/png" in out
    assert "bytes" in out


def test_body_truncated_to_max_chars() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="x" * 100)

    out = _skill(handler).run({"url": "https://example.com", "max_chars": 10})
    assert "[truncated]" in out


def test_validation() -> None:
    skill = HttpRequest()
    assert "required" in skill.run({})
    assert "http" in skill.run({"url": "ftp://example.com"}).lower()
    assert "method" in skill.run({"url": "https://example.com", "method": "FETCH"})
    assert "headers" in skill.run({"url": "https://example.com", "headers": [1, 2]})
    assert "body" in skill.run({"url": "https://example.com", "body": 123})


def test_egress_floor_blocks_internal() -> None:
    # No client needed — the egress floor rejects before any request is built.
    out = HttpRequest().run({"url": "http://169.254.169.254/latest/meta-data/"})
    assert "blocked" in out


def test_get_retries_transient_transport_error_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("connection reset", request=request)
        return httpx.Response(200, text="recovered")

    out = _skill(handler).run({"url": "https://example.com/api"})
    assert calls["n"] == 2  # retried once
    assert "→ 200" in out and "recovered" in out


def test_get_retries_on_transient_5xx_status() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503 if calls["n"] == 1 else 200, text="body")

    out = _skill(handler).run({"url": "https://example.com/api"})
    assert calls["n"] == 2
    assert "→ 200" in out


def test_post_is_never_auto_retried() -> None:
    # A connection error on a mutating method must NOT retry (double-submit risk).
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("boom", request=request)

    out = _skill(handler).run({"url": "https://example.com/orders", "method": "POST"})
    assert calls["n"] == 1  # no retry
    assert out.startswith("error:")


def test_retry_gives_up_after_one_and_returns_last_status() -> None:
    # Persistent 503 → one retry, then the 503 is reported (not an infinite loop).
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, text="still down")

    out = _skill(handler).run({"url": "https://example.com/api"})
    assert calls["n"] == 2  # original + one retry, then stop
    assert "→ 503" in out


def test_dangerous_and_wired() -> None:
    from halia.presets import get_preset
    from halia.skills import DEFAULT_SKILLS, available_skills, default_registry

    assert HttpRequest().dangerous is True  # mutating → approval-gated
    assert "http_request" in available_skills()
    assert "http_request" in DEFAULT_SKILLS  # auto-joins the generalist
    assert default_registry().get("http_request") is not None
    qa = get_preset("qa")
    assert qa is not None and "http_request" in qa.skills
