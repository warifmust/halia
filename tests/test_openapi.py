"""Tests for the Swagger/OpenAPI docs-URL → spec-URL resolver."""

from __future__ import annotations

import json
from collections.abc import Callable

from halia.openapi import _looks_like_openapi, resolve_openapi_spec

SPEC = json.dumps({"openapi": "3.0.0", "info": {"title": "X"}, "paths": {"/pet": {}}})


def _fetch(mapping: dict[str, str]) -> Callable[[str], str]:
    def f(url: str) -> str:
        if url in mapping:
            return mapping[url]
        raise ValueError(f"404 {url}")
    return f


def test_already_a_json_spec_returns_itself() -> None:
    url = "https://api.x/openapi.json"
    assert resolve_openapi_spec(url, _fetch({url: SPEC})) == url


def test_swagger_ui_referenced_url() -> None:
    html = '<html><script>SwaggerUIBundle({ url: "/v3/api-docs", dom_id: "#s" })</script></html>'
    m = {"https://api.x/docs": html, "https://api.x/v3/api-docs": SPEC}
    assert resolve_openapi_spec("https://api.x/docs", _fetch(m)) == "https://api.x/v3/api-docs"


def test_redoc_relative_spec_url_resolved() -> None:
    html = '<redoc spec-url="./openapi.json"></redoc>'
    m = {"https://api.x/docs/": html, "https://api.x/docs/openapi.json": SPEC}
    assert resolve_openapi_spec("https://api.x/docs/", _fetch(m)) == "https://api.x/docs/openapi.json"


def test_common_path_fallback_when_no_reference() -> None:
    m = {"https://api.x/docs": "<html><body>Swagger UI</body></html>",
         "https://api.x/openapi.json": SPEC}
    assert resolve_openapi_spec("https://api.x/docs", _fetch(m)) == "https://api.x/openapi.json"


def test_follows_swagger_initializer_js() -> None:
    # Modern Swagger UI: the spec URL lives in a separate swagger-initializer.js, not the HTML.
    index = '<html><script src="./swagger-initializer.js"></script></html>'
    init = 'window.ui = SwaggerUIBundle({ url: "https://api.x/api/v3/openapi.json" });'
    m = {
        "https://api.x/": index,
        "https://api.x/swagger-initializer.js": init,
        "https://api.x/api/v3/openapi.json": SPEC,
    }
    assert resolve_openapi_spec("https://api.x/", _fetch(m)) == "https://api.x/api/v3/openapi.json"


def test_non_spec_page_returns_none() -> None:
    m = {"https://example.com/blog": "<html><body>hello world</body></html>"}
    assert resolve_openapi_spec("https://example.com/blog", _fetch(m)) is None


def test_swagger_page_but_no_spec_found_returns_none() -> None:
    m = {"https://api.x/docs": "<html>swagger ui, but spec is nowhere</html>"}
    assert resolve_openapi_spec("https://api.x/docs", _fetch(m)) is None


def test_looks_like_openapi() -> None:
    assert _looks_like_openapi(SPEC)
    assert not _looks_like_openapi("<html>not json</html>")
    assert not _looks_like_openapi('{"foo": 1}')  # JSON, but no openapi/swagger key
