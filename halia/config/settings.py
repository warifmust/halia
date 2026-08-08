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
from dataclasses import dataclass, field
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
    # How the API key is sent: "Bearer" (Authorization: Bearer <key>), "api-key"
    # (api-key: <key> header), or "x-api-key" (Anthropic-style).
    auth_header: str = "Bearer"
    # For providers that need their own class: "openai_compat" (default) or "anthropic".
    provider_kind: str = "openai_compat"
    # Where the user can get an API key (shown in the setup wizard).
    key_url: str = ""
    # Optional note shown in the setup wizard (e.g. about consumer subscriptions).
    note: str = ""
    # Curated model list for the radio-button picker (first = default).
    models: list[str] = field(default_factory=list)


PROVIDERS: dict[str, ProviderSpec] = {
    "openai": ProviderSpec(
        "https://api.openai.com/v1", "OPENAI_API_KEY", "gpt-4o-mini",
        key_url="https://platform.openai.com/api-keys",
        note="API access is separate from a ChatGPT Plus subscription — billed per use.",
        models=[
            "gpt-5.2-pro", "gpt-5.2", "gpt-4.1-nano", "gpt-4o", "gpt-4o-mini",
            "o1", "o1-mini", "o1-preview", "gpt-4-turbo", "gpt-4", "gpt-3.5-turbo",
        ],
    ),
    "deepseek": ProviderSpec(
        "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY", "deepseek-v4-flash",
        key_url="https://platform.deepseek.com/api_keys",
        models=["deepseek-v4-flash", "deepseek-v4-pro"],
    ),
    "openrouter": ProviderSpec(
        "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "deepseek-v4-flash",
        key_url="https://openrouter.ai/keys",
        note="One key → many models. Load credits once. Select 'Custom model…' to enter any model.",
        models=[
            "deepseek-v4-flash", "mimo-v2.5", "mimo-v2.5-pro", "hy3",
            "deepseek-v4-pro", "gpt-5.6-sol", "gpt-5.6-luna", "claude-opus-5",
            "grok-4.5", "gemini-3.5-flash", "gpt-oss-120b", "qwen-3.6-plus",
            "Custom model…",
        ],
    ),
    "mimo": ProviderSpec(
        "https://api.xiaomimimo.com/v1", "MIMO_API_KEY", "mimo-v2.5",
        auth_header="api-key",
        key_url="https://mimo.ai/dashboard",
        models=["mimo-v2.5", "mimo-v2.5-pro"],
    ),
    "anthropic": ProviderSpec(
        "https://api.anthropic.com/v1", "ANTHROPIC_API_KEY", "claude-sonnet-5",
        auth_header="x-api-key", provider_kind="anthropic",
        key_url="https://console.anthropic.com/settings/keys",
        note="API access is separate from a claude.ai Pro subscription — billed per use.",
        models=[
            "claude-sonnet-5", "claude-opus-4.8", "claude-opus-4.6",
            "claude-sonnet-4.6", "claude-3.5-sonnet", "claude-3.5-haiku",
            "claude-3-opus", "claude-3-sonnet",
        ],
    ),
}


@dataclass(frozen=True)
class Config:
    """The resolved, ready-to-use configuration for one run."""

    provider: str
    model: str
    base_url: str
    api_key: str
    auth_header: str = "Bearer"
    provider_kind: str = "openai_compat"


class ConfigError(RuntimeError):
    """Raised when configuration is missing or invalid."""


# ── file store ────────────────────────────────────────────────────────────────
def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data: Any = json.loads(path.read_text())
    return data if isinstance(data, dict) else {}


def read_config() -> dict[str, Any]:
    """The raw (non-secret) config dict, or {} if none."""
    return _read_json(CONFIG_FILE)


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

    return Config(
        provider=provider, model=model, base_url=base_url, api_key=api_key,
        auth_header=spec.auth_header, provider_kind=spec.provider_kind,
    )


# ── trusted directories ───────────────────────────────────────────────────────
def get_trusted_dirs() -> list[str]:
    """List of directories the user has trusted (read + write allowed)."""
    data = _read_json(CONFIG_FILE)
    dirs = data.get("trusted_dirs")
    return dirs if isinstance(dirs, list) else []


def trust_directory(path: str) -> None:
    """Add a directory to the trusted list (persists to config)."""
    import os

    resolved = os.path.abspath(os.path.expanduser(path))
    dirs = get_trusted_dirs()
    if resolved not in dirs:
        dirs.append(resolved)
        data = _read_json(CONFIG_FILE)
        data["trusted_dirs"] = dirs
        write_config(data)


def is_trusted(path: str) -> bool:
    """Check if a path is under any trusted directory."""
    import os

    resolved = os.path.abspath(os.path.expanduser(path))
    for trusted in get_trusted_dirs():
        if resolved == trusted or resolved.startswith(trusted + "/"):
            return True
    return False
