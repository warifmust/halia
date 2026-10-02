"""Interactive ``halia mcp setup`` wizard — add servers and store their secrets.

Writes ``~/.halia/mcp.json`` and, for any ``${secret:…}`` / ``${input:…}``
reference, prompts (hidden input) to store the value in ``~/.halia/secrets.json``.
"""

from __future__ import annotations

import json
import re
from typing import Any

from rich.console import Console

from halia.cli.input import ask, pick
from halia.mcp.config import has_secret, read_raw, servers_file, write_raw


def mcp_setup(console: Console) -> None:
    """Interactively configure MCP servers + secrets, writing mcp.json."""
    console.print("[bold]MCP setup[/bold] — configure Model Context Protocol servers.\n")

    raw = read_raw()
    servers_block = raw.get("mcpServers", raw)
    # Unwrap a stray `servers` wrapper; normalize so mcpServers is a dict we can mutate.
    if isinstance(servers_block, dict) and isinstance(servers_block.get("servers"), dict):
        servers_block = servers_block["servers"]
    if not isinstance(servers_block, dict):
        servers_block = {}
    raw["mcpServers"] = servers_block

    while True:
        name = ask("Server name (e.g. notion, github): ").strip()
        if not name:
            break
        kind = pick("Transport:", ["HTTP (url)", "stdio (command)"], default=0)
        spec: dict[str, Any] = {}
        if kind.startswith("stdio"):
            spec["command"] = ask("Command (e.g. npx): ").strip()
            args_raw = ask("Args, space-separated (e.g. -y @playwright/mcp): ").strip()
            if args_raw:
                spec["args"] = args_raw.split()
            env: dict[str, str] = {}
            while True:
                key = ask("Env var name (blank to finish): ").strip()
                if not key:
                    break
                hint = "Value for '" + key + "' (use ${secret:name} for a stored secret): "
                value = ask(hint).strip()
                if value:
                    env[key] = value
            if env:
                spec["env"] = env
        else:
            spec["url"] = ask("URL (e.g. https://mcp.notion.com/mcp): ").strip()
            auth = pick("Authentication:", ["None", "OAuth"], default=0)
            if auth.startswith("OAuth"):
                spec["auth"] = "oauth"
        desc = ask("Description, one line (optional): ").strip()
        if desc:
            spec["description"] = desc
        servers_block[name] = spec
        console.print(f"  [green]✓[/green] added [bold]{name}[/bold]")
        again = pick("Add another server?", ["Yes — add another", "No — done"], default=1)
        if again.startswith("No"):
            break

    write_raw(raw)
    _prompt_secrets(console, json.dumps(raw))
    console.print(f"\n[green]✓[/green] MCP config saved to {servers_file()}")
    console.print(
        "[dim]Verify with `halia mcp list` · store more keys anytime with "
        "`halia mcp set-key <name> <value>`.[/dim]"
    )


def _prompt_secrets(console: Console, blob: str) -> None:
    """Prompt (hidden) for any ${secret:…} / ${input:…} reference lacking a value."""
    from halia.config.settings import write_secret

    names = sorted(set(re.findall(r"\$\{(?:secret|input):([A-Za-z0-9_.\-]+)\}", blob)))
    missing = [n for n in names if not has_secret(n)]
    for name in missing:
        console.print(f"\n[cyan]Secret needed:[/cyan] [bold]{name}[/bold]")
        value = ask(f"  Value for '{name}' (leave blank to skip): ", is_password=True).strip()
        if value:
            write_secret(name, value)
            console.print(f"  [green]✓[/green] stored [bold]{name}[/bold]")
        else:
            console.print("  [dim]skipped.[/dim]")
