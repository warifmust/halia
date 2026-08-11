"""First-run UX: config is checked BEFORE the trust-directory prompt."""

from __future__ import annotations

from typing import Any

import pytest
import typer

from halia.cli.main import _require_config
from halia.config import settings


def test_require_config_exits_when_no_key(tmp_path: Any, monkeypatch: Any) -> None:
    # No config file and no key env vars → _require_config must raise (so the caller exits
    # with setup guidance) rather than fall through to the trust prompt.
    monkeypatch.setattr(settings, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(settings, "SECRETS_FILE", tmp_path / "secrets.json")
    for var in ("HALIA_API_KEY", "OPENAI_API_KEY", "HALIA_PROVIDER"):
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(typer.Exit):
        _require_config()


def test_require_config_passes_with_key(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(settings, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(settings, "SECRETS_FILE", tmp_path / "secrets.json")
    monkeypatch.setenv("HALIA_API_KEY", "sk-test")

    _require_config()  # must not raise
