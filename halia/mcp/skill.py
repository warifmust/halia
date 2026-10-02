"""McpSkill — adapts one MCP tool to halia's synchronous Skill protocol."""

from __future__ import annotations

import re
from typing import Any, Protocol

from halia.mcp.client import _McpTool
from halia.mcp.config import McpServer

_NAME_LIMIT = 64  # OpenAI function names cap at 64 chars


class _McpCaller(Protocol):
    """The call surface McpSkill needs from a manager (McpManager satisfies it)."""

    def call_tool(self, server: str, tool: str, arguments: dict[str, Any]) -> str: ...


def _sanitize(raw: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", raw) or "tool"


def mcp_tool_name(server: str, tool: str) -> str:
    """A provider-safe function name for an MCP tool: ``mcp__<server>__<tool>``."""
    prefix = f"mcp__{_sanitize(server)}__"
    budget = max(1, _NAME_LIMIT - len(prefix))
    return f"{prefix}{_sanitize(tool)[:budget]}"


class McpSkill:
    """One MCP tool callable by the agent under the name ``mcp__<server>__<tool>``."""

    dangerous = False  # the MCP server owns its own authorisation
    untrusted = True  # output comes from outside halia → quarantined

    def __init__(self, manager: _McpCaller, tool: _McpTool) -> None:
        self._manager = manager
        self._tool = tool
        self.name = mcp_tool_name(tool.server, tool.name)
        self.description = (
            tool.description
            or f"MCP tool '{tool.name}' exposed by the '{tool.server}' server."
        )
        self.parameters = tool.input_schema

    def run(self, args: dict[str, Any]) -> str:
        return self._manager.call_tool(self._tool.server, self._tool.name, args)


class McpConnectSkill:
    """Control-plane tool: load an MCP server's tools on demand (lazy mode).

    In lazy mode no MCP tools are registered up front — the agent calls this to
    connect a server (running its OAuth flow if needed) and then the server's
    ``mcp__<server>__<tool>`` tools appear. ``run`` is synchronous and blocks the
    turn while it connects, which is what makes an interactive OAuth login work.
    """

    name = "mcp_connect"
    description = (
        "Load the tools of an MCP server so they can be used. "
        "Servers are listed in the system prompt. Call this before using any "
        "mcp__<server>__<tool> tool. If the server needs OAuth login, a browser "
        "will open."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "server": {
                "type": "string",
                "description": "The MCP server name to connect, e.g. 'notion' or 'github'.",
            }
        },
        "required": ["server"],
    }
    dangerous = False
    untrusted = False

    def __init__(self, manager: Any, registry: Any, servers: list[McpServer]) -> None:
        self._manager = manager
        self._registry = registry
        self._servers = {s.name: s for s in servers}

    def run(self, args: dict[str, Any]) -> str:
        server = str(args.get("server", ""))
        spec = self._servers.get(server)
        if spec is None:
            known = ", ".join(sorted(self._servers)) or "(none)"
            return f"error: unknown MCP server '{server}'. Available: {known}"
        status = self._manager.connect_all([spec])
        detail = status.servers.get(server, "error: not connected")
        if detail != "ok":
            return f"error: could not connect '{server}': {detail.removeprefix('error: ')}"
        for tool in self._manager.tools():
            self._registry.register(McpSkill(self._manager, tool))
        tools = [
            mcp_tool_name(t.server, t.name)
            for t in self._manager.tools()
            if t.server == server
        ]
        listing = ", ".join(tools)[:500]
        return f"connected '{server}' — {len(tools)} tool(s) available: {listing}"
