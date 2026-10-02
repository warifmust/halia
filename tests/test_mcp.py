"""Tests for the MCP integration (config parsing, skill adapter, registration)."""

from __future__ import annotations

import json
from typing import Any

import pytest

import halia.mcp as mcp
from halia.config import settings
from halia.mcp.client import McpStatus, _friendly_error, _McpTool, _result_to_text
from halia.mcp.config import McpServer, has_secret, load_servers, read_raw, remove_server
from halia.mcp.oauth import JsonTokenStorage, _LoopbackCallback, has_tokens
from halia.mcp.skill import McpConnectSkill, McpSkill, mcp_tool_name
from halia.skills.registry import SkillRegistry


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Point the MCP registry + secrets at a temp dir; reset module singletons."""
    monkeypatch.setattr(settings, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(settings, "SECRETS_FILE", tmp_path / "secrets.json")
    monkeypatch.setattr(mcp, "_manager", None)
    monkeypatch.setattr(mcp, "_announced", False)


def _write_servers(tmp_path: Any, data: dict[str, Any]) -> None:
    (settings.CONFIG_DIR / "mcp.json").write_text(json.dumps(data), encoding="utf-8")


# --- config ---------------------------------------------------------------


def test_load_servers_parses_stdio_and_http(tmp_path: Any) -> None:
    _write_servers(
        tmp_path,
        {
            "mcpServers": {
                "github": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-github"],
                    "env": {"GITHUB_TOKEN": "ghp_123"},
                },
                "jira": {
                    "url": "https://mcp.example.com/jira",
                    "headers": {"Authorization": "Bearer abc"},
                },
            }
        },
    )
    servers = load_servers()
    assert [s.name for s in servers] == ["github", "jira"]
    github = servers[0]
    assert github.command == "npx"
    assert github.args == ["-y", "@modelcontextprotocol/server-github"]
    assert github.env == {"GITHUB_TOKEN": "ghp_123"}
    assert github.url is None
    jira = servers[1]
    assert jira.url == "https://mcp.example.com/jira"
    assert jira.headers == {"Authorization": "Bearer abc"}


def test_load_servers_skips_malformed(tmp_path: Any) -> None:
    _write_servers(
        tmp_path,
        {
            "mcpServers": {
                "ok": {"command": "echo"},
                "no_backend": {"description": "nothing"},
                "not_a_dict": "hi",
                "empty": {},
            }
        },
    )
    servers = load_servers()
    assert [s.name for s in servers] == ["ok"]


def test_load_servers_accepts_bare_top_level_map(tmp_path: Any) -> None:
    _write_servers(tmp_path, {"git": {"command": "git-mcp"}})
    servers = load_servers()
    assert [s.name for s in servers] == ["git"]


def test_load_servers_parses_oauth_auth(tmp_path: Any) -> None:
    _write_servers(
        tmp_path, {"mcpServers": {"notion": {"url": "https://x/mcp", "auth": "oauth"}}}
    )
    servers = load_servers()
    assert servers[0].auth == "oauth"
    assert servers[0].url == "https://x/mcp"


def test_read_raw_missing_and_invalid(tmp_path: Any) -> None:
    assert read_raw() == {}
    (settings.CONFIG_DIR / "mcp.json").write_text("{not json", encoding="utf-8")
    assert read_raw() == {}


def test_remove_server(tmp_path: Any) -> None:
    _write_servers(tmp_path, {"mcpServers": {"a": {"command": "x"}, "b": {"command": "y"}}})
    assert remove_server("a") is True
    assert [s.name for s in load_servers()] == ["b"]
    assert remove_server("missing") is False


def test_load_servers_unwraps_vscode_servers_wrapper(tmp_path: Any) -> None:
    _write_servers(
        tmp_path,
        {
            "mcpServers": {
                "servers": {"github": {"url": "https://api.githubcopilot.com/mcp/"}}
            },
            "inputs": [{"type": "promptString", "id": "github_mcp_pat"}],
        },
    )
    servers = load_servers()
    assert [s.name for s in servers] == ["github"]
    assert servers[0].url == "https://api.githubcopilot.com/mcp/"


def test_remove_server_unwraps_wrapper(tmp_path: Any) -> None:
    _write_servers(tmp_path, {"mcpServers": {"servers": {"github": {"url": "https://x"}}}})
    assert remove_server("github") is True
    assert load_servers() == []


def test_resolve_env_secret_and_input(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from halia.config.settings import write_secret

    monkeypatch.setenv("HALIA_TEST_TOKEN", "env-token")
    write_secret("github_mcp_pat", "ghp_secret")
    _write_servers(
        tmp_path,
        {
            "mcpServers": {
                "a": {
                    "url": "https://x/mcp",
                    "headers": {"Authorization": "Bearer ${secret:github_mcp_pat}"},
                },
                "b": {
                    "url": "https://y/mcp",
                    "headers": {"Authorization": "Bearer ${env:HALIA_TEST_TOKEN}"},
                },
                "c": {
                    "url": "https://z/mcp",
                    "headers": {"Authorization": "Bearer ${input:github_mcp_pat}"},
                },
                "d": {
                    "url": "https://w/mcp",
                    "headers": {"Authorization": "Bearer ${secret:missing}"},
                },
            }
        },
    )
    by_name = {s.name: s for s in load_servers()}
    assert by_name["a"].headers["Authorization"] == "Bearer ghp_secret"
    assert by_name["b"].headers["Authorization"] == "Bearer env-token"
    assert by_name["c"].headers["Authorization"] == "Bearer ghp_secret"  # input alias
    assert by_name["d"].headers["Authorization"] == "Bearer "  # unresolved → empty


def test_resolve_plain_string_secret(tmp_path: Any) -> None:
    # Hand-edited secrets.json with a bare string (not the {"api_key": ...} shape).
    (settings.SECRETS_FILE).write_text(
        json.dumps({"github_mcp_pat": "ghp_plain"}), encoding="utf-8"
    )
    _write_servers(
        tmp_path,
        {
            "mcpServers": {
                "github": {
                    "url": "https://x/mcp",
                    "headers": {"Authorization": "Bearer ${secret:github_mcp_pat}"},
                }
            }
        },
    )
    servers = load_servers()
    assert servers[0].headers["Authorization"] == "Bearer ghp_plain"


# --- skill adapter --------------------------------------------------------


class _FakeManager:
    def call_tool(self, server: str, tool: str, args: dict[str, Any]) -> str:
        return f"called {server}/{tool} {sorted(args)}"


def test_mcp_tool_name_sanitizes() -> None:
    assert mcp_tool_name("github", "create-issue") == "mcp__github__create_issue"
    assert mcp_tool_name("my server", "tool") == "mcp__my_server__tool"


def test_mcp_skill_delegates_to_manager() -> None:
    tool = _McpTool(
        server="github",
        name="create-issue",
        description="Open an issue",
        input_schema={"type": "object", "properties": {"title": {"type": "string"}}},
    )
    skill = McpSkill(_FakeManager(), tool)
    assert skill.name == "mcp__github__create_issue"
    assert skill.dangerous is False
    assert skill.untrusted is True
    assert skill.parameters == tool.input_schema
    assert skill.run({"title": "bug"}) == "called github/create-issue ['title']"


# --- registration ---------------------------------------------------------


def test_register_mcp_skills_no_servers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp, "load_servers", lambda: [])
    status = mcp.register_mcp_skills(SkillRegistry())
    assert status.servers == {}
    assert status.announce is False


def test_register_mcp_skills_missing_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp, "load_servers", lambda: [McpServer(name="git", command="git-mcp")])
    monkeypatch.setattr(mcp, "mcp_available", lambda: False)
    registry = SkillRegistry()
    status = mcp.register_mcp_skills(registry)
    assert status.servers["git"].startswith("error:")
    assert registry.all() == []
    assert status.announce is True


def test_register_mcp_skills_registers_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    servers = [McpServer(name="git", command="git-mcp")]
    tool = _McpTool(
        server="git", name="commit", description="Commit", input_schema={}
    )

    class _Fake:
        def __init__(self, console: Any = None) -> None:
            pass

        def connect_all(self, _servers: list[McpServer]) -> McpStatus:
            status = McpStatus()
            status.add("git", "ok", tools=1)
            return status

        def tools(self) -> list[_McpTool]:
            return [tool]

    monkeypatch.setattr(mcp, "load_servers", lambda: servers)
    monkeypatch.setattr(mcp, "mcp_available", lambda: True)
    monkeypatch.setattr(mcp, "load_mode", lambda: "eager")
    monkeypatch.setattr(mcp, "get_manager", _Fake)
    registry = SkillRegistry()
    status = mcp.register_mcp_skills(registry)
    assert status.servers == {"git": "ok"}
    assert status.tool_count == 1
    assert status.announce is True
    assert registry.get("mcp__git__commit") is not None


def test_register_announces_only_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp, "load_servers", lambda: [McpServer(name="git", command="git-mcp")])
    monkeypatch.setattr(mcp, "mcp_available", lambda: True)

    class _Fake:
        def __init__(self, console: Any = None) -> None:
            pass

        def connect_all(self, _servers: list[McpServer]) -> McpStatus:
            return McpStatus()

        def tools(self) -> list[_McpTool]:
            return []

    monkeypatch.setattr(mcp, "get_manager", _Fake)
    first = mcp.register_mcp_skills(SkillRegistry())
    second = mcp.register_mcp_skills(SkillRegistry())
    assert first.announce is True
    assert second.announce is False


# --- lazy mode ------------------------------------------------------------


def test_load_mode_defaults_lazy(tmp_path: Any) -> None:
    _write_servers(tmp_path, {"mcpServers": {"a": {"command": "x"}}})
    assert mcp.load_mode() == "lazy"
    _write_servers(tmp_path, {"mcpServers": {"a": {"command": "x"}}, "mode": "eager"})
    assert mcp.load_mode() == "eager"
    _write_servers(tmp_path, {"mcpServers": {"a": {"command": "x"}}, "mode": "bogus"})
    assert mcp.load_mode() == "lazy"


def test_mcp_system_block(tmp_path: Any) -> None:
    _write_servers(
        tmp_path,
        {"mcpServers": {"git": {"command": "x", "description": "local git"}}, "mode": "eager"},
    )
    assert mcp.mcp_system_block() == ""  # eager mode: no index
    _write_servers(
        tmp_path,
        {
            "mcpServers": {"git": {"command": "x", "description": "local git"}},
            "mode": "lazy",
        },
    )
    block = mcp.mcp_system_block()
    assert "git" in block and "local git" in block and "mcp_connect" in block


def test_register_mcp_skills_lazy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    _write_servers(
        tmp_path,
        {"mcpServers": {"git": {"command": "git-mcp"}}, "mode": "lazy"},
    )
    monkeypatch.setattr(mcp, "mcp_available", lambda: True)

    class _Fake:
        def __init__(self, console: Any = None) -> None:
            pass

        def connect_all(self, _servers: list[McpServer]) -> McpStatus:
            raise AssertionError("lazy mode must not connect at startup")

        def tools(self) -> list[_McpTool]:
            return []

    monkeypatch.setattr(mcp, "get_manager", _Fake)
    registry = SkillRegistry()
    status = mcp.register_mcp_skills(registry)
    assert status.servers == {"git": "lazy"}
    assert registry.get("mcp_connect") is not None
    assert registry.get("mcp__git__commit") is None


def test_mcp_connect_skill(tmp_path: Any) -> None:
    servers = [McpServer(name="git", command="git-mcp")]
    tool = _McpTool(server="git", name="commit", description="C", input_schema={})

    class _FakeManager:
        def __init__(self) -> None:
            self.connected = False

        def connect_all(self, _servers: list[McpServer]) -> McpStatus:
            self.connected = True
            status = McpStatus()
            status.add("git", "ok", tools=1)
            return status

        def tools(self) -> list[_McpTool]:
            return [tool] if self.connected else []

    manager = _FakeManager()
    registry = SkillRegistry()
    skill = McpConnectSkill(manager, registry, servers)
    assert skill.name == "mcp_connect"
    assert "unknown MCP server" in skill.run({"server": "nope"})
    out = skill.run({"server": "git"})
    assert "connected 'git'" in out and "mcp__git__commit" in out
    assert registry.get("mcp__git__commit") is not None


# --- error / result formatting -------------------------------------------


def test_friendly_error_auth() -> None:
    assert "expired or revoked" in _friendly_error(RuntimeError("401 Unauthorized"))


def test_friendly_error_not_found() -> None:
    assert "not found on PATH" in _friendly_error(FileNotFoundError("no such file: npx"))


def test_result_to_text_text_content() -> None:
    class _Block:
        type = "text"
        text = "hello"

    class _Result:
        is_error = False
        structured_content = None
        content = [_Block()]

    assert _result_to_text(_Result()) == "hello"


def test_result_to_text_error() -> None:
    class _Block:
        type = "text"
        text = "boom"

    class _Result:
        is_error = True
        structured_content = None
        content = [_Block()]

    assert _result_to_text(_Result()) == "error: boom"


def test_has_secret(tmp_path: Any) -> None:
    from halia.config.settings import write_secret

    assert has_secret("nope") is False
    write_secret("github_mcp_pat", "ghp_123")
    assert has_secret("github_mcp_pat") is True
    # A hand-edited plain-string entry is accepted too.
    settings.SECRETS_FILE.write_text(json.dumps({"plain_key": "val"}), encoding="utf-8")
    assert has_secret("plain_key") is True


def test_prompt_secrets_stores_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from rich.console import Console

    import halia.config.settings as cfg_settings
    from halia.mcp.setup import _prompt_secrets

    stored: dict[str, str] = {}
    monkeypatch.setattr(
        "halia.mcp.setup.ask",
        lambda prompt, is_password=False: "tok123",
    )
    monkeypatch.setattr(cfg_settings, "write_secret", lambda k, v: stored.__setitem__(k, v))
    blob = '{"mcpServers":{"a":{"headers":{"Authorization":"Bearer ${secret:github_mcp_pat}"}}}}'
    _prompt_secrets(Console(), blob)
    assert stored == {"github_mcp_pat": "tok123"}


# --- OAuth ----------------------------------------------------------------


def test_json_token_storage_roundtrip(tmp_path: Any) -> None:
    import asyncio

    from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

    async def _run() -> None:
        storage = JsonTokenStorage("notion")
        assert await storage.get_tokens() is None
        token = OAuthToken(
            access_token="acc",
            token_type="Bearer",
            refresh_token="ref",
            expires_in=3600,
            scope="read",
        )
        await storage.set_tokens(token)
        got = await storage.get_tokens()
        assert got is not None
        assert got.access_token == "acc" and got.refresh_token == "ref"
        info = OAuthClientInformationFull(client_id="cid", client_secret="sec")
        await storage.set_client_info(info)
        cinfo = await storage.get_client_info()
        assert cinfo is not None and cinfo.client_id == "cid"
        assert has_tokens("notion") is True
        assert has_tokens("other") is False

    asyncio.run(_run())


def test_loopback_callback(tmp_path: Any) -> None:
    import asyncio
    import urllib.request

    async def _run() -> None:
        cb = _LoopbackCallback()
        await cb.start()
        try:
            uri = f"{cb.redirect_uri}?code=abc123&state=st&iss=https%3A%2F%2Fiss"

            def _hit() -> None:
                urllib.request.urlopen(uri).read()

            await asyncio.to_thread(_hit)
            result = await cb.wait_for_code()
            assert result.code == "abc123"
            assert result.state == "st"
            assert result.iss == "https://iss"
        finally:
            await cb.close()

    asyncio.run(_run())
