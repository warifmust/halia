"""Async MCP bridge: a worker event loop + one persistent connection per server.

The MCP Python SDK is loop-bound — its sessions are async context managers that
can't cross threads — while halia's agent loop is synchronous. This bridge runs a
dedicated event loop on a daemon thread and exposes a synchronous surface via
``run_coroutine_threadsafe`` (the same pattern the CUA embedded host uses).
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Coroutine
from dataclasses import dataclass, field
from typing import Any

from halia.mcp.config import McpServer

_CONNECT_TIMEOUT = 120.0  # generous: npx/uvx first run may download the server
_OAUTH_CONNECT_TIMEOUT = 300.0  # interactive: the user has to click through auth
_CALL_TIMEOUT = 300.0


@dataclass(frozen=True)
class _McpTool:
    server: str
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class McpStatus:
    """Result of connecting configured servers. Never raised — reported instead."""

    servers: dict[str, str] = field(default_factory=dict)  # name -> "ok" | "error: …"
    tool_count: int = 0
    announce: bool = False  # True only on the first registration in a process

    def add(self, name: str, detail: str, tools: int = 0) -> None:
        self.servers[name] = detail
        self.tool_count += tools


def _normalize_schema(schema: Any) -> dict[str, Any]:
    """Coerce a tool's input_schema into a safe OpenAI 'object' schema."""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    out: dict[str, Any] = dict(schema)
    out.setdefault("type", "object")
    out.setdefault("properties", {})
    return out


def _friendly_error(exc: BaseException) -> str:
    """Turn a connect/call exception into a short, actionable reason."""
    text = str(exc).strip() or exc.__class__.__name__
    low = text.lower()
    if any(s in low for s in ("401", "403", "unauthorized", "forbidden")):
        return f"{text} — auth failed: token/PAT expired or revoked?"
    if any(s in low for s in ("no such file", "executable", "command not found")):
        return f"{text} — command not found on PATH?"
    if any(s in low for s in ("connection", "refused", "resolve", "timeout")):
        return f"{text} — server unreachable?"
    return text


def _result_to_text(result: Any) -> str:
    """Serialize a CallToolResult into the string the agent observes."""
    is_error = bool(getattr(result, "is_error", False))
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        body = json.dumps(structured, default=str, ensure_ascii=False)
        return f"error: {body}" if is_error else body
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(str(text))
        else:
            block_type = getattr(block, "type", None)
            parts.append(f"[{block_type}]" if block_type else str(block))
    body = "\n".join(p for p in parts if p)
    if is_error:
        return f"error: {body}" if body else "error: MCP tool returned an error"
    return body or "(no content)"


class _Connection:
    """One connected server, owned by the worker loop."""

    def __init__(self, spec: McpServer, console: Any) -> None:
        self.spec = spec
        self._console = console
        self.session: Any = None
        self.tools: list[_McpTool] = []
        self._cleanup: list[Any] = []

    async def connect(self) -> None:
        if self.spec.url:
            await self._connect_http()
        else:
            await self._connect_stdio()
        assert self.session is not None
        # Enter the session: __aenter__ starts the dispatcher task that reads
        # responses — without it, the first request raises "send_raw_request
        # called before run()". Register its exit so close() unwinds it first.
        await self.session.__aenter__()
        self._cleanup.append(lambda: self.session.__aexit__(None, None, None))
        await self.session.initialize()
        result = await self.session.list_tools()
        self.tools = [
            _McpTool(
                server=self.spec.name,
                name=t.name,
                description=t.description or "",
                input_schema=_normalize_schema(t.input_schema),
            )
            for t in (result.tools or [])
        ]

    async def _connect_stdio(self) -> None:
        from mcp import ClientSession
        from mcp.client.stdio import (
            StdioServerParameters,
            get_default_environment,
            stdio_client,
        )

        env: dict[str, str] | None = None
        if self.spec.env:
            env = {**get_default_environment(), **self.spec.env}
        params = StdioServerParameters(
            command=self.spec.command or "",
            args=self.spec.args,
            env=env,
        )
        cm: Any = stdio_client(params)
        read, write = await cm.__aenter__()
        self._cleanup.append(lambda: cm.__aexit__(None, None, None))
        self.session = ClientSession(read, write)

    async def _connect_http(self) -> None:
        import httpx2
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        auth: Any = None
        oauth_callback: Any = None
        if self.spec.auth == "oauth":
            from halia.mcp.oauth import build_oauth_auth

            auth, oauth_callback = await build_oauth_auth(self.spec, self._console)
            if oauth_callback is not None:
                self._cleanup.append(lambda: oauth_callback.close())

        http_client = httpx2.AsyncClient(headers=dict(self.spec.headers), auth=auth)
        try:
            cm: Any = streamable_http_client(self.spec.url or "", http_client=http_client)
            read, write = await cm.__aenter__()
        except Exception:
            await http_client.aclose()
            raise
        # Append in reverse-run order: close() unwinds the list back-to-front,
        # so the HTTP client closes last (after the stream context is exited).
        self._cleanup.append(lambda: http_client.aclose())
        self._cleanup.append(lambda: cm.__aexit__(None, None, None))
        self.session = ClientSession(read, write)

    async def close(self) -> None:
        for hook in reversed(self._cleanup):
            try:
                result = hook()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                pass
        self._cleanup = []
        self.session = None


class McpManager:
    """Synchronous facade over a daemon event loop holding MCP sessions."""

    def __init__(self, console: Any = None) -> None:
        if console is None:
            from rich.console import Console

            console = Console()
        self._console = console
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, name="halia-mcp", daemon=True
        )
        self._thread.start()
        self._connections: dict[str, _Connection] = {}

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _submit(self, coro: Coroutine[Any, Any, Any], timeout: float) -> Any:
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def connect_all(self, servers: list[McpServer]) -> McpStatus:
        """Connect every server; failures become status entries, never exceptions."""
        status = McpStatus()
        for spec in servers:
            existing = self._connections.get(spec.name)
            if existing is not None:
                status.add(spec.name, "ok", len(existing.tools))
                continue
            timeout = _OAUTH_CONNECT_TIMEOUT if spec.auth == "oauth" else _CONNECT_TIMEOUT
            try:
                self._submit(self._connect(spec), timeout)
                conn = self._connections[spec.name]
                status.add(spec.name, "ok", len(conn.tools))
            except Exception as exc:
                status.add(spec.name, f"error: {_friendly_error(exc)}")
        return status

    async def _connect(self, spec: McpServer) -> None:
        conn = _Connection(spec, self._console)
        await conn.connect()
        self._connections[spec.name] = conn

    def call_tool(self, server: str, tool: str, arguments: dict[str, Any]) -> str:
        conn = self._connections.get(server)
        if conn is None or conn.session is None:
            return f"error: MCP server '{server}' is not connected"
        try:
            result = self._submit(
                conn.session.call_tool(tool, arguments or {}), _CALL_TIMEOUT
            )
        except Exception as exc:
            return f"error: MCP call failed: {_friendly_error(exc)}"
        return _result_to_text(result)

    def tools(self) -> list[_McpTool]:
        out: list[_McpTool] = []
        for conn in self._connections.values():
            out.extend(conn.tools)
        return out

    def connected_names(self) -> set[str]:
        """Names of servers currently connected (for lazy-mode routing)."""
        return set(self._connections)

    def close(self) -> None:
        if not self._loop.is_running():
            return

        async def _close_all() -> None:
            for conn in self._connections.values():
                await conn.close()
            self._connections.clear()

        try:
            self._submit(_close_all(), 10.0)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5.0)
