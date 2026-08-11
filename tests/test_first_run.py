"""First-run UX: config is checked AFTER the trust-directory prompt.

When no config exists, the user is offered an inline setup wizard instead of
a dead-end error message.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
import typer

from halia.cli.main import _ensure_config
from halia.config import settings


def test_ensure_config_exits_when_no_key_and_user_declines(tmp_path: Any, monkeypatch: Any) -> None:
    # No config file and no key env vars → _ensure_config must exit
    # (the user is prompted but we simulate declining).
    monkeypatch.setattr(settings, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(settings, "SECRETS_FILE", tmp_path / "secrets.json")
    for var in ("HALIA_API_KEY", "OPENAI_API_KEY", "HALIA_PROVIDER"):
        monkeypatch.delenv(var, raising=False)

    with patch("halia.cli.input.pick", return_value="no — exit"):
        with pytest.raises(typer.Exit):
            _ensure_config()


def test_ensure_config_passes_with_key(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(settings, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(settings, "SECRETS_FILE", tmp_path / "secrets.json")
    monkeypatch.setenv("HALIA_API_KEY", "sk-test")

    _ensure_config()  # must not raise
