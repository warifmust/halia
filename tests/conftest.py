"""Shared fixtures for the test suite."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from halia.config import settings


@pytest.fixture(autouse=True)
def isolated_halia_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Point halia's config store at a temp dir for every test.

    The setup wizard, the CUA/MCP installers, and `halia upgrade` all *write*
    ``~/.halia/config.json`` (the durable record of which extras the environment
    needs). Without this, a test that exercises them would edit the developer's
    real installation.
    """
    monkeypatch.setattr(settings, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(settings, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(settings, "SECRETS_FILE", tmp_path / "secrets.json")
    yield
