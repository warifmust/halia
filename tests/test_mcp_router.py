"""Tests for the MCP router (system-1) and its config."""

from __future__ import annotations

from typing import Any

import pytest

import halia.core.agent as agent
from halia.config import settings
from halia.config.settings import Config, load_config
from halia.mcp.config import McpServer
from halia.mcp.router import _parse_servers, route_servers


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setattr(settings, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(settings, "SECRETS_FILE", tmp_path / "secrets.json")
    for var in ("HALIA_ROUTER_MODEL", "HALIA_PROVIDER", "HALIA_MODEL", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_load_config_router_model(tmp_path: Any) -> None:
    settings.CONFIG_FILE.write_text(
        '{"provider": "openai", "model": "gpt-4o-mini", "router_model": "tiny/one"}'
    )
    settings.SECRETS_FILE.write_text('{"openai": {"api_key": "sk-test"}}')
    assert load_config().router_model == "tiny/one"


def test_load_config_router_model_defaults_none(tmp_path: Any) -> None:
    settings.CONFIG_FILE.write_text('{"provider": "openai", "model": "gpt-4o-mini"}')
    settings.SECRETS_FILE.write_text('{"openai": {"api_key": "sk-test"}}')
    assert load_config().router_model is None


def test_parse_servers_forms() -> None:
    assert _parse_servers('{"servers": ["notion", "github"]}') == ["notion", "github"]
    assert _parse_servers('["notion"]') == ["notion"]
    assert _parse_servers("notion") == ["notion"]
    assert _parse_servers("```json\n{\"servers\": [\"notion\"]}\n```") == ["notion"]
    # A bare token is treated as one name; route_servers checks validity.
    assert _parse_servers("garbage") == ["garbage"]
    assert _parse_servers('{"servers": []}') == []


def test_route_servers_uses_router_model(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _FakeResult:
        content = '{"servers": ["notion", "bogus"]}'
        tool_calls: list[Any] = []
        usage = None

    def _fake_build(cfg: Config) -> Any:
        captured["router_config_model"] = cfg.model

        class _Provider:
            def chat(self, _messages: Any, tools: Any = None, on_delta: Any = None) -> Any:
                return _FakeResult()

        return _Provider()

    monkeypatch.setattr(agent, "build_provider", _fake_build)
    config = Config(
        provider="openai", model="big", base_url="u", api_key="k", router_model="tiny/one"
    )
    servers = [McpServer(name="notion", url="https://x"), McpServer(name="github", command="gh")]
    names = route_servers("open my notes", config, servers, console=None)
    assert names == ["notion"]  # "bogus" filtered out
    assert captured["router_config_model"] == "tiny/one"


def test_route_servers_no_router() -> None:
    config = Config(provider="openai", model="big", base_url="u", api_key="k")
    servers = [McpServer(name="notion", url="https://x")]
    assert route_servers("open my notes", config, servers, console=None) == []
