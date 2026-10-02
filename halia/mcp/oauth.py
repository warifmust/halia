"""OAuth 2.1 support for HTTP MCP servers.

MCP servers that require OAuth (like Notion's hosted MCP) return ``401`` with a
``WWW-Authenticate`` header pointing at their authorization server. halia plugs
the SDK's ``OAuthClientProvider`` (DCR + authorization-code-with-PKCE + refresh)
into the HTTP transport, so the agent can connect to OAuth servers transparently.

The interactive bit — popping a browser and waiting for the redirect — is done
with a tiny loopback HTTP server. Tokens and the registered client are persisted
to ``~/.halia/mcp_tokens.json`` (mode 0600), keyed by server name.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from halia.config import settings
from halia.mcp.config import McpServer


def tokens_file() -> Path:
    """Path to the OAuth token store (``~/.halia/mcp_tokens.json``)."""
    return settings.CONFIG_DIR / "mcp_tokens.json"


def has_tokens(server_name: str) -> bool:
    """True when persisted OAuth tokens exist for a server (sync, for the CLI)."""
    path = tokens_file()
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    entry = data.get(server_name)
    return isinstance(entry, dict) and bool(entry.get("tokens"))


class JsonTokenStorage:
    """A ``TokenStorage`` persisting OAuth tokens + client registration to disk."""

    def __init__(self, server_name: str) -> None:
        self._name = server_name

    def _read(self) -> dict[str, Any]:
        path = tokens_file()
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, Any]) -> None:
        path = tokens_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:  # pragma: no cover - non-POSIX filesystems
            pass

    async def get_tokens(self) -> Any:
        from mcp.shared.auth import OAuthToken

        raw = self._read().get(self._name, {}).get("tokens")
        return OAuthToken.model_validate(raw) if isinstance(raw, dict) else None

    async def set_tokens(self, tokens: Any) -> None:
        data = self._read()
        data.setdefault(self._name, {})["tokens"] = tokens.model_dump(mode="json")
        self._write(data)

    async def get_client_info(self) -> Any:
        from mcp.shared.auth import OAuthClientInformationFull

        raw = self._read().get(self._name, {}).get("client_info")
        if isinstance(raw, dict):
            return OAuthClientInformationFull.model_validate(raw)
        return None

    async def set_client_info(self, client_info: Any) -> None:
        data = self._read()
        data.setdefault(self._name, {})["client_info"] = client_info.model_dump(mode="json")
        self._write(data)


class _LoopbackCallback:
    """A loopback HTTP server that receives the OAuth redirect and returns the code."""

    def __init__(self) -> None:
        self._server: Any = None
        self._future: asyncio.Future[Any] | None = None
        self.redirect_uri = ""

    async def start(self) -> None:
        from mcp.client.auth import AuthorizationCodeResult

        loop = asyncio.get_running_loop()
        self._future = loop.create_future()

        async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                request_line = await reader.readline()
                while True:
                    line = await reader.readline()
                    if line in (b"\r\n", b"\n", b""):
                        break
                path = request_line.decode(errors="replace").split(" ")[1]
                query = parse_qs(urlparse(path).query)
                code = (query.get("code") or [""])[0]
                state = (query.get("state") or [""])[0]
                iss_values = query.get("iss")
                iss: str | None = iss_values[0] if iss_values else None
                body = (
                    b"<html><body style='font-family: sans-serif'>"
                    b"<h3>halia is authorized</h3>"
                    b"<p>You can close this tab and return to the terminal.</p>"
                    b"</body></html>"
                )
                writer.write(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: text/html; charset=utf-8\r\n"
                    b"Content-Length: "
                    + str(len(body)).encode()
                    + b"\r\nConnection: close\r\n\r\n"
                    + body
                )
                await writer.drain()
                if self._future is not None and not self._future.done():
                    self._future.set_result(
                        AuthorizationCodeResult(code=code, state=state, iss=iss)
                    )
            except Exception as exc:  # noqa: BLE001 — surface as a failed flow
                if self._future is not None and not self._future.done():
                    self._future.set_exception(exc)
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:  # noqa: BLE001
                    pass

        self._server = await asyncio.start_server(_handle, "127.0.0.1", 0)
        sock = self._server.sockets[0]
        port = int(sock.getsockname()[1])
        self.redirect_uri = f"http://127.0.0.1:{port}/callback"

    async def wait_for_code(self) -> Any:
        if self._future is None:
            raise RuntimeError("OAuth callback server not started")
        return await self._future

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None


async def build_oauth_auth(
    spec: McpServer, console: Any
) -> tuple[Any, _LoopbackCallback]:
    """Build an httpx2 auth provider + its loopback callback for an OAuth server."""
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthClientMetadata
    from pydantic import AnyUrl

    callback = _LoopbackCallback()
    await callback.start()
    storage = JsonTokenStorage(spec.name)

    async def redirect_handler(url: str) -> None:
        import webbrowser

        console.print(
            f"[cyan]🌐 MCP OAuth[/cyan] — authorize halia for [bold]{spec.name}[/bold]:"
        )
        console.print(f"  [link={url}]{url}[/link]")
        console.print(
            "  [dim]If your browser doesn't open, copy the link above.[/dim]"
        )
        await asyncio.to_thread(webbrowser.open, url)

    async def callback_handler() -> Any:
        return await callback.wait_for_code()

    client_metadata = OAuthClientMetadata(
        redirect_uris=[AnyUrl(callback.redirect_uri)],
        token_endpoint_auth_method="none",
        client_name="halia",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
    )
    provider = OAuthClientProvider(
        server_url=spec.url or "",
        client_metadata=client_metadata,
        storage=storage,
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )
    return provider, callback
