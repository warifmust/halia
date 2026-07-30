"""Configuration loading (minimal, pre-wizard).

Resolves the active provider/model/base_url/api_key from, in order of precedence:
environment variables → `~/.halia/config.toml` → built-in provider defaults.

This is deliberately thin so `halia ask` works today; the `setup` wizard (which
will WRITE this config + manage secrets at 0600, never hand-edited) comes next.
Secrets are read from the environment here and never written to disk by this module.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONFIG_DIR = Path.home() / ".halia"
CONFIG_FILE = CONFIG_DIR / "config.toml"


@dataclass(frozen=True)
class ProviderSpec:
    """Built-in defaults for a known provider."""

    base_url: str
    key_env: str
    default_model: str


PROVIDERS: dict[str, ProviderSpec] = {
    "openai": ProviderSpec("https://api.openai.com/v1", "OPENAI_API_KEY", "gpt-4o-mini"),
    "deepseek": ProviderSpec("https://api.deepseek.com/v1", "DEEPSEEK_API_KEY", "deepseek-chat"),
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


def _load_file() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return {}
    return tomllib.loads(CONFIG_FILE.read_text())


def load_config() -> Config:
    """Resolve the active configuration, or raise ConfigError with guidance."""
    file_data = _load_file()

    provider = os.environ.get("HALIA_PROVIDER") or file_data.get("provider") or "openai"
    if provider not in PROVIDERS:
        known = ", ".join(sorted(PROVIDERS))
        raise ConfigError(f"unknown provider '{provider}'. Known providers: {known}.")
    spec = PROVIDERS[provider]

    model = os.environ.get("HALIA_MODEL") or file_data.get("model") or spec.default_model
    base_url = os.environ.get("HALIA_BASE_URL") or file_data.get("base_url") or spec.base_url
    api_key = os.environ.get("HALIA_API_KEY") or os.environ.get(spec.key_env) or ""

    if not api_key:
        raise ConfigError(
            f"no API key for provider '{provider}'. "
            f"Set {spec.key_env} (or HALIA_API_KEY) in your environment. "
            "A `halia setup` wizard will manage this for you later."
        )

    return Config(provider=provider, model=model, base_url=base_url, api_key=api_key)
