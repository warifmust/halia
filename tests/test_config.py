"""Tests for configuration resolution and the managed file/secret store."""

import stat
from typing import Any

import pytest

from halia.config import settings
from halia.config.settings import (
    ConfigError,
    load_config,
    read_secret,
    write_config,
    write_secret,
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Point config/secrets at a temp dir and clear all relevant env vars."""
    monkeypatch.setattr(settings, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(settings, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(settings, "SECRETS_FILE", tmp_path / "secrets.json")
    for var in (
        "HALIA_PROVIDER",
        "HALIA_MODEL",
        "HALIA_BASE_URL",
        "HALIA_API_KEY",
        "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


def test_load_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HALIA_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    cfg = load_config()
    assert cfg.provider == "deepseek"
    assert cfg.base_url == "https://api.deepseek.com/v1"
    assert cfg.model == "deepseek-v4-flash"
    assert cfg.api_key == "sk-test"


def test_missing_key_raises() -> None:
    with pytest.raises(ConfigError, match="no API key"):
        load_config()


def test_unknown_provider_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HALIA_PROVIDER", "bogus")
    with pytest.raises(ConfigError, match="unknown provider"):
        load_config()


def test_load_from_files() -> None:
    write_config({"provider": "deepseek", "model": "deepseek-v4-flash"})
    write_secret("deepseek", "sk-file")
    cfg = load_config()
    assert cfg.provider == "deepseek"
    assert cfg.model == "deepseek-v4-flash"
    assert cfg.api_key == "sk-file"


def test_secret_file_is_0600() -> None:
    write_secret("openai", "sk-x")
    mode = stat.S_IMODE(settings.SECRETS_FILE.stat().st_mode)
    assert mode == 0o600
    assert read_secret("openai") == "sk-x"


def test_env_key_overrides_file(monkeypatch: pytest.MonkeyPatch) -> None:
    write_config({"provider": "deepseek"})
    write_secret("deepseek", "sk-file")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
    assert load_config().api_key == "sk-env"
