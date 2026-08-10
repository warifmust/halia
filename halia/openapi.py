"""Resolve a Swagger/OpenAPI *docs* URL to its machine-readable spec.

Users naturally teach the docs page they see in a browser (e.g. `…/docs/idp`), which is
usually Swagger-UI or Redoc HTML — not the spec. This finds the underlying OpenAPI JSON so
`save_reference` / `/teach` store something the model can actually reason over (paths,
params, schemas) rather than rendered HTML.

Bounded + deterministic: at most a handful of egress-floored fetches (the page, then the
spec URL referenced in it, then a few conventional paths). JSON specs only — YAML specs
should be taught by their direct URL.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from urllib.parse import urljoin

# Where a Swagger-UI / Redoc page points at its spec, most specific first.
_SPEC_URL_PATTERNS = [
    re.compile(r'SwaggerUIBundle\s*\(\s*\{.*?\burl\s*:\s*["\']([^"\']+)["\']', re.DOTALL),
    re.compile(r'\bspec-url\s*=\s*["\']([^"\']+)["\']'),  # <redoc spec-url="…">
    re.compile(r'["\']([^"\']*(?:openapi|swagger|api-docs)[^"\']*\.(?:json|yaml|yml))["\']'),
    re.compile(r'\burl\s*:\s*["\']([^"\']+\.(?:json|yaml|yml))["\']'),
]

# Conventional spec locations to probe relative to the docs URL.
_COMMON_PATHS = [
    "openapi.json", "swagger.json", "v3/api-docs", "api-docs", "swagger/v1/swagger.json",
]


def _looks_like_openapi(body: str) -> bool:
    """True if `body` is a JSON object with an `openapi`/`swagger` top-level key."""
    if not body.lstrip().startswith("{"):
        return False
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return False
    return isinstance(data, dict) and ("openapi" in data or "swagger" in data)


def _extract_spec_url(html: str, base_url: str) -> str | None:
    for pat in _SPEC_URL_PATTERNS:
        m = pat.search(html)
        if m:
            return str(urljoin(base_url, m.group(1).strip()))
    return None


def _candidate_paths(url: str) -> list[str]:
    base = url if url.endswith("/") else url.rsplit("/", 1)[0] + "/"
    return [urljoin(base, p) for p in _COMMON_PATHS]


def resolve_openapi_spec(url: str, fetch_raw: Callable[[str], str] | None = None) -> str | None:
    """Return the OpenAPI spec URL for `url`, or None if it isn't (or doesn't point to) a spec.

    `fetch_raw(u) -> body` is injectable for tests; by default it uses the egress-floored
    fetch. Returns `url` itself when it's already a JSON spec, the referenced/probed spec URL
    for a Swagger-UI/Redoc page, or None otherwise (caller then stores the page as-is).
    """
    if fetch_raw is None:
        from halia.skills.web import fetch_url_raw

        def fetch_raw(u: str) -> str:
            return fetch_url_raw(u)[1]

    try:
        body = fetch_raw(url)
    except Exception:  # noqa: BLE001 — any fetch failure just means "can't resolve", fall back
        return None

    if _looks_like_openapi(body):
        return url
    if not any(k in body.lower() for k in ("swagger", "openapi", "redoc")):
        return None  # not a spec and not a recognizable docs page

    candidates: list[str] = []
    referenced = _extract_spec_url(body, url)
    if referenced:
        candidates.append(referenced)
    # Modern Swagger UI keeps its config in a separate swagger-initializer.js — follow it.
    init_m = re.search(
        r'src\s*=\s*["\']([^"\']*(?:initializer|swagger-config)[^"\']*\.js)["\']', body
    )
    if init_m:
        init_url = urljoin(url, init_m.group(1))
        try:
            from_init = _extract_spec_url(fetch_raw(init_url), init_url)
            if from_init and from_init not in candidates:
                candidates.append(from_init)
        except Exception:  # noqa: BLE001 — initializer unreachable, fall through to probing
            pass
    candidates += [c for c in _candidate_paths(url) if c not in candidates]

    for cand in candidates:
        try:
            if _looks_like_openapi(fetch_raw(cand)):
                return cand
        except Exception:  # noqa: BLE001 — try the next candidate
            continue
    return None
