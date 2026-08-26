"""Tests for the halia upgrade command and its helpers."""

from __future__ import annotations

from unittest.mock import patch

import httpx
from typer.testing import CliRunner

from halia import __version__ as CURRENT_VERSION
from halia.cli.main import app
from halia.upgrade import is_newer, parse_version

runner = CliRunner()


def test_parse_version() -> None:
    assert parse_version("0.26.19") == (0, 26, 19)
    assert parse_version("1.0.0") == (1, 0, 0)
    assert parse_version("0.26.19rc1") == (0, 26, 19)


def test_is_newer() -> None:
    assert is_newer("0.26.20", "0.26.19") is True
    assert is_newer("0.27.0", "0.26.19") is True
    assert is_newer("1.0.0", "0.99.99") is True
    assert is_newer("0.26.19", "0.26.19") is False
    assert is_newer("0.26.18", "0.26.19") is False


def test_fetch_latest_version_parses_version() -> None:
    from halia.upgrade import fetch_latest_version

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text='"""halia."""\n\n__version__ = "0.99.0"\n')

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert fetch_latest_version(client=client) == "0.99.0"


def test_upgrade_already_latest() -> None:
    with patch("halia.upgrade.fetch_latest_version", return_value=CURRENT_VERSION):
        result = runner.invoke(app, ["upgrade"])
    assert result.exit_code == 0
    assert "Already up to date" in result.output


def test_upgrade_check_reports_newer_without_install() -> None:
    with patch("halia.upgrade.fetch_latest_version", return_value="0.99.0"), \
         patch("halia.upgrade.perform_upgrade") as mock_install:
        result = runner.invoke(app, ["upgrade", "--check"])
    assert result.exit_code == 0
    assert "newer version is available" in result.output
    mock_install.assert_not_called()


def test_upgrade_installs_when_confirmed() -> None:
    with patch("halia.upgrade.fetch_latest_version", return_value="0.99.0"), \
         patch("halia.upgrade.perform_upgrade", return_value=(True, "installed")) as mock_install:
        result = runner.invoke(app, ["upgrade", "--yes"])
    assert result.exit_code == 0
    assert "upgraded to" in result.output
    assert "0.99.0" in result.output
    mock_install.assert_called_once()


def test_upgrade_failure_exits_nonzero() -> None:
    with patch("halia.upgrade.fetch_latest_version", return_value="0.99.0"), \
         patch("halia.upgrade.perform_upgrade", return_value=(False, "boom")):
        result = runner.invoke(app, ["upgrade", "--yes"])
    assert result.exit_code == 1
    assert "Upgrade failed" in result.output
    assert "boom" in result.output
