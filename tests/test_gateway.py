"""Tests for the send-first gateway (config + Telegram send, mocked HTTP)."""

from typing import Any

import httpx

import halia.config.settings as settings
import halia.gateway as gw


def _isolate(tmp_path: Any) -> None:
    """Point the config + secrets files at a temp dir."""
    settings.CONFIG_DIR = tmp_path
    settings.CONFIG_FILE = tmp_path / "config.json"
    settings.SECRETS_FILE = tmp_path / "secrets.json"


def test_not_configured_by_default(tmp_path: Any) -> None:
    _isolate(tmp_path)
    assert gw.get_gateway() is None
    ok, detail = gw.notify("hi")
    assert ok is False and "no gateway" in detail


def test_save_and_load_gateway(tmp_path: Any) -> None:
    _isolate(tmp_path)
    gw.save_gateway("telegram", "12345", "bottoken")
    loaded = gw.get_gateway()
    assert loaded is not None
    assert loaded.channel == "telegram" and loaded.chat_id == "12345"


def test_missing_token_means_not_configured(tmp_path: Any) -> None:
    _isolate(tmp_path)
    # channel/chat_id present but no token → unusable
    settings.write_config({"gateway": {"channel": "telegram", "chat_id": "1"}})
    assert gw.get_gateway() is None


def test_unknown_channel_rejected(tmp_path: Any) -> None:
    _isolate(tmp_path)
    try:
        gw.save_gateway("carrier-pigeon", "1", "t")
    except ValueError as exc:
        assert "unknown channel" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_notify_sends_to_telegram(tmp_path: Any) -> None:
    _isolate(tmp_path)
    gw.save_gateway("telegram", "555", "SECRET-TOKEN")
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ok, detail = gw.notify("hello team", client=client)
    assert ok is True and detail == "sent"
    assert "/botSECRET-TOKEN/sendMessage" in seen["url"]
    assert '"chat_id":"555"' in seen["body"]
    assert "hello team" in seen["body"]


def test_notify_error_redacts_token(tmp_path: Any) -> None:
    _isolate(tmp_path)
    gw.save_gateway("telegram", "555", "SECRET-TOKEN")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="Unauthorized: SECRET-TOKEN leaked here")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ok, detail = gw.notify("x", client=client)
    assert ok is False
    assert "SECRET-TOKEN" not in detail  # token scrubbed from the error surface
    assert "***" in detail
