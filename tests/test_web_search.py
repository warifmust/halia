"""Tests for the web_search skill (offline — parsing + a mocked HTTP client)."""

import httpx

from halia.skills.web import WebSearch, parse_search_results

# A trimmed DuckDuckGo HTML results page (two results, redirect-wrapped URLs).
_HTML = """
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fpython.org%2Fdec&amp;rut=x">
    <b>decimal</b> — Decimal arithmetic
  </a>
  <a class="result__snippet" href="#">The decimal module provides exact decimal arithmetic.</a>
</div>
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fmoney&amp;rut=y">
    Money in Python
  </a>
  <a class="result__snippet" href="#">Use Decimal, not float, for money.</a>
</div>
"""


def test_parse_extracts_title_url_snippet() -> None:
    results = parse_search_results(_HTML, max_results=5)
    assert len(results) == 2
    title, url, snippet = results[0]
    assert title == "decimal — Decimal arithmetic"  # tags stripped, entities unescaped
    assert url == "https://python.org/dec"  # redirect resolved
    assert "exact decimal arithmetic" in snippet


def test_parse_respects_max_results() -> None:
    assert len(parse_search_results(_HTML, max_results=1)) == 1


def test_parse_empty_page() -> None:
    assert parse_search_results("<html>no results here</html>", max_results=5) == []


def _mock_client(html: str, status: int = 200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=html)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_web_search_formats_results() -> None:
    skill = WebSearch(client=_mock_client(_HTML))
    out = skill.run({"query": "python decimal"})
    assert "2 result(s) for 'python decimal'" in out
    assert "https://python.org/dec" in out
    assert "Money in Python" in out


def test_web_search_requires_query() -> None:
    assert "required" in WebSearch(client=_mock_client(_HTML)).run({"query": "  "})


def test_web_search_handles_no_results() -> None:
    out = WebSearch(client=_mock_client("<html/>")).run({"query": "zxqw"})
    assert "no results" in out


def test_web_search_reports_http_error() -> None:
    out = WebSearch(client=_mock_client("busy", status=503)).run({"query": "x"})
    assert "HTTP 503" in out


def test_web_search_is_safe() -> None:
    assert WebSearch().dangerous is False


def test_web_search_in_default_and_research() -> None:
    from halia.presets import get_preset
    from halia.skills import available_skills, default_registry

    assert "web_search" in available_skills()
    assert default_registry().get("web_search") is not None
    assert "web_search" in get_preset("research").skills
