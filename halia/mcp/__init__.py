"""MCP integration: expose external MCP servers' tools to the agent as skills.

Servers are configured in ``~/.halia/mcp.json``; each tool is registered as
``mcp__<server>__<tool>``. Connecting is best-effort — failures are reported in
the returned status and never block a run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from halia.mcp.client import McpManager, McpStatus
from halia.mcp.config import McpServer, load_mode, load_servers, servers_file, write_raw
from halia.mcp.oauth import has_tokens, tokens_file
from halia.mcp.skill import McpConnectSkill, McpSkill

if TYPE_CHECKING:
    from halia.skills.registry import SkillRegistry

__all__ = [
    "McpManager",
    "McpServer",
    "McpSkill",
    "McpStatus",
    "close_manager",
    "get_manager",
    "has_tokens",
    "load_mode",
    "load_servers",
    "mcp_available",
    "mcp_system_block",
    "register_mcp_skills",
    "servers_file",
    "tokens_file",
    "write_raw",
]

_manager: McpManager | None = None
_announced = False


def mcp_available() -> bool:
    """True when the optional ``mcp`` package is importable."""
    try:
        import mcp  # noqa: F401
    except ImportError:
        return False
    return True


def get_manager(console: Any = None) -> McpManager:
    """The process-wide MCP manager (worker loop + sessions), created lazily."""
    global _manager
    if _manager is None:
        _manager = McpManager(console=console)
    return _manager


def close_manager() -> None:
    """Close all MCP sessions and stop the worker loop.

    Call this at a clean shutdown point (end of `chat`/`run`). It is deliberately
    NOT registered with `atexit`: the MCP HTTP transport's session termination
    needs the event-loop thread pool, which Python shuts down before atexit
    handlers run — closing there raises "cannot schedule new futures after
    shutdown". Relying on process exit to drop connections is safe and silent.
    """
    global _manager
    if _manager is not None:
        _manager.close()
        _manager = None


def mcp_system_block() -> str:
    """A short MCP server index for the system prompt (empty in eager mode)."""
    if load_mode() != "lazy":
        return ""
    servers = load_servers()
    if not servers:
        return ""
    lines = [
        "MCP servers are available but NOT loaded yet. Call the mcp_connect tool "
        "with a server name to load its tools before using them:"
    ]
    for spec in servers:
        hint = f" — {spec.description}" if spec.description else ""
        lines.append(f"- {spec.name}{hint}")
    return "\n".join(lines)


def register_mcp_skills(registry: SkillRegistry, console: Any = None) -> McpStatus:
    """Connect configured MCP servers and register their tools on ``registry``.

    In lazy mode (the default) nothing connects up front — only the
    ``mcp_connect`` control tool is registered, and each server's tools load on
    demand. In eager mode (``"mode": "eager"``) every server connects at
    startup. Never raises: a missing ``mcp`` package or a broken server is
    recorded in the returned status and its tools are simply not added.
    ``announce`` is True only on the first call, so the CLI prints one banner
    per process.
    """
    global _announced
    servers = load_servers()
    status = McpStatus()
    if not servers:
        return status
    if not mcp_available():
        detail = (
            "error: `mcp` package missing — run `uv tool install --force "
            "'git+https://github.com/warifmust/halia.git@main' --with mcp`"
        )
        for spec in servers:
            status.add(spec.name, detail)
    elif load_mode() == "lazy":
        manager = get_manager(console)
        registry.register(McpConnectSkill(manager, registry, servers))
        for spec in servers:
            status.add(spec.name, "lazy")
    else:
        manager = get_manager(console)
        status = manager.connect_all(servers)
        for tool in manager.tools():
            registry.register(McpSkill(manager, tool))
    status.announce = not _announced
    _announced = True
    return status
