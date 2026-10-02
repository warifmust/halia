"""MCP server registry — ``~/.halia/mcp.json``.

The file follows the VS Code / Claude ``mcpServers`` shape::

    {
      "mcpServers": {
        "github": {
          "command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-github"],
          "env": {"GITHUB_TOKEN": "..."}
        },
        "jira": {
          "url": "https://mcp.example.com/jira",
          "headers": {"Authorization": "Bearer ..."}
        }
      }
    }

A server is either a stdio subprocess (``command`` + ``args``) or a
streamable-HTTP endpoint (``url`` + optional ``headers``). The file is edited by
hand or via ``halia mcp edit``.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from halia.config import settings
from halia.config.settings import read_secret


def servers_file() -> Path:
    """Path to the MCP registry file (``~/.halia/mcp.json``)."""
    return settings.CONFIG_DIR / "mcp.json"


@dataclass(frozen=True)
class McpServer:
    """One configured MCP server: a stdio subprocess or a streamable-HTTP endpoint."""

    name: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    auth: str | None = None  # "oauth" for OAuth-authorized HTTP servers
    description: str = ""  # human hint for the lazy-mode server index


_VAR_RE = re.compile(r"\$\{(env|secret|input):([A-Za-z0-9_.\-]+)\}")


def _secret_value(key: str) -> str:
    """A secret from ~/.halia/secrets.json, accepting both stored shapes."""
    value = read_secret(key)
    if value:
        return value
    # Hand-edited secrets.json may store a bare string instead of write_secret's
    # {"api_key": ...} shape — accept both.
    try:
        data = json.loads(settings.SECRETS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    entry = data.get(key)
    return entry if isinstance(entry, str) else ""


def _resolve(value: str) -> str:
    """Expand ``${env:NAME}`` / ``${secret:NAME}`` / ``${input:NAME}``.

    ``secret`` and ``input`` both read from ``~/.halia/secrets.json``; ``input``
    is a VS Code compat alias so files exported from VS Code/Claude work
    unchanged. Unresolvable variables expand to the empty string.
    """

    def _sub(match: re.Match[str]) -> str:
        kind, key = match.group(1), match.group(2)
        if kind == "env":
            return os.environ.get(key, "")
        return _secret_value(key)

    return _VAR_RE.sub(_sub, value)


def _as_str_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): _resolve(str(v)) for k, v in value.items()}


def read_raw() -> dict[str, Any]:
    """Parse mcp.json; ``{}`` when missing or unreadable (never raises)."""
    path = servers_file()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_raw(data: dict[str, Any]) -> None:
    """Write the registry file, creating ``~/.halia`` if needed."""
    path = servers_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def load_mode() -> str:
    """MCP loading mode: ``"lazy"`` (default — connect on demand) or ``"eager"``."""
    mode = read_raw().get("mode")
    return "eager" if mode == "eager" else "lazy"


def has_secret(key: str) -> bool:
    """True when a ``${secret:key}`` / ``${input:key}`` reference resolves to a value."""
    return bool(_secret_value(key))


def load_servers() -> list[McpServer]:
    """Turn mcp.json into McpServer entries, skipping anything malformed."""
    raw = read_raw()
    block = raw.get("mcpServers", raw)
    if isinstance(block, dict):
        # Tolerate a stray `"servers"` wrapper — a common VS Code export mistake.
        inner = block.get("servers")
        if isinstance(inner, dict):
            block = inner
    if not isinstance(block, dict):
        return []
    servers: list[McpServer] = []
    for name, spec in block.items():
        if not isinstance(name, str) or not isinstance(spec, dict):
            continue
        command = spec.get("command")
        url = spec.get("url")
        if not command and not url:
            continue
        args = spec.get("args", [])
        servers.append(
            McpServer(
                name=name,
                command=_resolve(str(command)) if isinstance(command, str) else None,
                args=[_resolve(str(a)) for a in args] if isinstance(args, list) else [],
                env=_as_str_map(spec.get("env")),
                url=_resolve(str(url)) if isinstance(url, str) else None,
                headers=_as_str_map(spec.get("headers")),
                auth=str(spec["auth"]) if isinstance(spec.get("auth"), str) else None,
                description=str(spec.get("description", "")),
            )
        )
    return servers


def remove_server(name: str) -> bool:
    """Delete one server by name; returns True if it was present."""
    raw = read_raw()
    servers_block = raw.get("mcpServers")
    if isinstance(servers_block, dict) and isinstance(servers_block.get("servers"), dict):
        servers_block = servers_block["servers"]
    if isinstance(servers_block, dict) and name in servers_block:
        del servers_block[name]
    elif name in raw:
        del raw[name]
    else:
        return False
    write_raw(raw)
    return True
