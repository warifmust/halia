"""Gateway — send-first outbound notifications (no daemon).

The gateway pushes a run's result or an alert to the user on a channel they configure
(Telegram first). It is deliberately SEND-ONLY: one outbound HTTPS call, no persistent
listener — so it needs no daemon and works from a headless/scheduled run. Interactive
two-way chat over a channel is the only piece that would need a listener, and that's
deferred (see the requirements doc, "Gateway").

Config split (same pattern as provider keys): the channel + chat id live in the plain
config file; the bot token lives in the 0600 secrets file. `notify()` is the one call
scheduled runs and `--notify` use.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from halia.config.settings import read_config, read_secret, write_config, write_secret

_TIMEOUT = 15.0
CHANNELS = ("telegram",)


@dataclass(frozen=True)
class GatewayConfig:
    """A configured outbound channel."""

    channel: str
    chat_id: str


def get_gateway() -> GatewayConfig | None:
    """The configured gateway, or None if not set up (or missing its token)."""
    entry = read_config().get("gateway")
    if not isinstance(entry, dict):
        return None
    channel = entry.get("channel")
    chat_id = entry.get("chat_id")
    if channel not in CHANNELS or not isinstance(chat_id, str) or not chat_id:
        return None
    if not read_secret("gateway"):  # token missing → not usable
        return None
    return GatewayConfig(channel=channel, chat_id=chat_id)


def save_gateway(channel: str, chat_id: str, token: str) -> None:
    """Persist the channel + chat id (config) and the bot token (0600 secrets)."""
    if channel not in CHANNELS:
        raise ValueError(f"unknown channel '{channel}'. Supported: {', '.join(CHANNELS)}")
    data = read_config()
    data["gateway"] = {"channel": channel, "chat_id": chat_id}
    write_config(data)
    write_secret("gateway", token)


def notify(text: str, client: httpx.Client | None = None) -> tuple[bool, str]:
    """Send `text` on the configured channel. Returns (ok, detail); never raises."""
    gw = get_gateway()
    if gw is None:
        return False, "no gateway configured (run `halia gateway setup`)"
    token = read_secret("gateway")
    if not token:
        return False, "gateway token missing"

    if gw.channel == "telegram":
        return _send_telegram(token, gw.chat_id, text, client)
    return False, f"channel '{gw.channel}' not implemented"


def _send_telegram(
    token: str, chat_id: str, text: str, client: httpx.Client | None
) -> tuple[bool, str]:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    owns = client is None
    http = client or httpx.Client(timeout=_TIMEOUT)
    try:
        resp = http.post(url, json={"chat_id": chat_id, "text": text})
    except httpx.HTTPError as exc:
        return False, f"send failed: {exc}"
    finally:
        if owns:
            http.close()
    if resp.status_code == 200:
        return True, "sent"
    # Telegram returns a JSON description on error — surface it, minus the token.
    detail = resp.text[:200].replace(token, "***")
    return False, f"telegram error HTTP {resp.status_code}: {detail}"
