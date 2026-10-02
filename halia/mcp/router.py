"""System-1 router: preload the MCP servers a request is likely to need.

Optional. When `router_model` is set (via `halia setup` or `~/.halia/config.json`),
this module asks that cheap/fast model which of the configured MCP servers are
relevant to the user's request, so lazy mode can preload them before the main
agent runs. It's a hint, never a gate — a wrong or empty answer just means the
agent falls back to `mcp_connect`.
"""

from __future__ import annotations

import json
import re
from typing import Any

from halia.config.settings import Config
from halia.mcp.config import McpServer

_ROUTER_SYSTEM = (
    "You route user requests to MCP servers. Reply with ONLY a JSON object in "
    'the form {"servers": ["<name>", ...]}, listing only the servers clearly '
    "needed. If none are relevant, reply with {\"servers\": []}."
)


def _server_index(servers: list[McpServer]) -> str:
    lines: list[str] = []
    for spec in servers:
        hint = f" — {spec.description}" if spec.description else ""
        lines.append(f"- {spec.name}{hint}")
    return "\n".join(lines)


def _parse_servers(text: str) -> list[str]:
    """Parse the router's reply into server names, tolerating sloppy output."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    data: Any = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\[[^\]]*\]|\{[^}]*\}", text)
        if match:
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                data = None
        elif text and re.fullmatch(r"[A-Za-z0-9_.\-]+", text):
            return [text]
    if isinstance(data, dict):
        data = data.get("servers", data.get("server", []))
    if isinstance(data, str):
        return [data]
    if isinstance(data, list):
        return [str(x) for x in data if isinstance(x, str)]
    return []


def route_servers(
    user_text: str, config: Config, servers: list[McpServer], console: Any
) -> list[str]:
    """Ask the router model which of `servers` the request needs. Never raises."""
    from dataclasses import replace

    from halia.core.agent import build_provider

    valid = {s.name for s in servers}
    if not config.router_model or not valid:
        return []
    router_config = replace(config, model=config.router_model)
    provider = build_provider(router_config)
    result = provider.chat(
        [
            {"role": "system", "content": _ROUTER_SYSTEM},
            {
                "role": "user",
                "content": f"Servers:\n{_server_index(servers)}\n\nUser request: {user_text}",
            },
        ]
    )
    content = result.content or ""
    names = [n for n in _parse_servers(content) if n in valid]
    if names and console is not None:
        console.print(f"[dim]🌐 router → {', '.join(names)}[/dim]")
    return names
