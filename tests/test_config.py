"""Tests for configuration resolution."""

import pytest

from halia.config import settings
from halia.config.settings import ConfigError, load_config


def test_load_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    monkeypatch.setattr(settings, "CONFIG_FILE", tmp_path / "nope.toml")  # type: ignore[operator]
    monkeypatch.setenv("HALIA_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.delenv("HALIA_MODEL", raising=False)
    monkeypatch.delenv("HALIA_BASE_URL", raising=False)
    monkeypatch.delenv("HALIA_API_KEY", raising=False)

    cfg = load_config()
    assert cfg.provider == "deepseek"
    assert cfg.base_url == "https://api.deepseek.com/v1"
    assert cfg.model == "deepseek-chat"
    assert cfg.api_key == "sk-test"


def test_missing_key_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    monkeypatch.setattr(settings, "CONFIG_FILE", tmp_path / "nope.toml")  # type: ignore[operator]
    monkeypatch.setenv("HALIA_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("HALIA_API_KEY", raising=False)

    with pytest.raises(ConfigError, match="no API key"):
        load_config()


def test_unknown_provider_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    monkeypatch.setattr(settings, "CONFIG_FILE", tmp_path / "nope.toml")  # type: ignore[operator]
    monkeypatch.setenv("HALIA_PROVIDER", "bogus")

    with pytest.raises(ConfigError, match="unknown provider"):
        load_config()
