"""Configuration + secret storage.

Resolution order for a run: environment variables → managed config files
(`~/.halia/config.json` + `~/.halia/secrets.json`) → built-in provider defaults.

The `setup` wizard WRITES these files (never hand-edited); secrets land in a
0600 file the tool owns — which works headless (servers, Docker, Proxmox) where
an OS keyring does not. Env vars still override, for CI / power users.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONFIG_DIR = Path.home() / ".halia"
CONFIG_FILE = CONFIG_DIR / "config.json"
SECRETS_FILE = CONFIG_DIR / "secrets.json"


@dataclass(frozen=True)
class ProviderSpec:
    """Built-in defaults for a known provider."""

    base_url: str
    key_env: str
    default_model: str


PROVIDERS: dict[str, ProviderSpec] = {
    "openai": ProviderSpec("https://api.openai.com/v1", "OPENAI_API_KEY", "gpt-4o-mini"),
    "deepseek": ProviderSpec(
        "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY", "deepseek-v4-flash"
    ),
}


@dataclass(frozen=True)
class Config:
    """The resolved, ready-to-use configuration for one run."""

    provider: str
    model: str
    base_url: str
    api_key: str


class ConfigError(RuntimeError):
    """Raised when configuration is missing or invalid."""


# ── file store ────────────────────────────────────────────────────────────────
def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data: Any = json.loads(path.read_text())
    return data if isinstance(data, dict) else {}


def write_config(data: dict[str, Any]) -> None:
    """Write the (non-secret) config file."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data, indent=2))


def read_secret(provider: str) -> str | None:
    """Read a provider's stored API key, if any."""
    entry = _read_json(SECRETS_FILE).get(provider)
    if isinstance(entry, dict):
        key = entry.get("api_key")
        if isinstance(key, str) and key:
            return key
    return None


def write_secret(provider: str, api_key: str) -> None:
    """Store a provider's API key in the 0600 secrets file."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = _read_json(SECRETS_FILE)
    existing = data.get(provider)
    entry: dict[str, Any] = existing if isinstance(existing, dict) else {}
    entry["api_key"] = api_key
    data[provider] = entry
    SECRETS_FILE.write_text(json.dumps(data, indent=2))
    SECRETS_FILE.chmod(0o600)


# ── resolve ───────────────────────────────────────────────────────────────────
def load_config() -> Config:
    """Resolve the active configuration, or raise ConfigError with guidance."""
    file_data = _read_json(CONFIG_FILE)

    provider = os.environ.get("HALIA_PROVIDER") or file_data.get("provider") or "openai"
    if provider not in PROVIDERS:
        known = ", ".join(sorted(PROVIDERS))
        raise ConfigError(f"unknown provider '{provider}'. Known providers: {known}.")
    spec = PROVIDERS[provider]

    model = os.environ.get("HALIA_MODEL") or file_data.get("model") or spec.default_model
    base_url = os.environ.get("HALIA_BASE_URL") or file_data.get("base_url") or spec.base_url
    api_key = (
        os.environ.get("HALIA_API_KEY")
        or os.environ.get(spec.key_env)
        or read_secret(provider)
        or ""
    )
    if not api_key:
        raise ConfigError(
            f"no API key for provider '{provider}'. Run `halia setup`, or set "
            f"{spec.key_env} (or HALIA_API_KEY) in your environment."
        )

    return Config(provider=provider, model=model, base_url=base_url, api_key=api_key)
