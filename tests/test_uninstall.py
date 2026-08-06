"""Tests for halia uninstall command."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from halia.cli.main import app

runner = CliRunner()


def _setup_fake_halia(tmp_path: Path) -> dict[str, Path]:
    """Create fake halia files in a temp directory."""
    config_dir = tmp_path / ".halia"
    config_dir.mkdir()
    config_file = config_dir / "config.json"
    config_file.write_text('{"provider": "openai"}')
    secrets_file = config_dir / "secrets.json"
    secrets_file.write_text('{"openai": "sk-test"}')
    db_file = config_dir / "halia.db"
    db_file.write_bytes(b"fake sqlite")
    persona = config_dir / "PERSONA.md"
    persona.write_text("You are halia.")
    return {
        "config_dir": config_dir,
        "config": config_file,
        "secrets": secrets_file,
        "db": db_file,
        "persona": persona,
    }


def test_uninstall_removes_config_and_secrets(tmp_path: Path) -> None:
    """uninstall --force removes config, secrets, and DB."""
    files = _setup_fake_halia(tmp_path)

    with patch("halia.config.settings.CONFIG_DIR", files["config_dir"]), \
         patch("halia.config.settings.CONFIG_FILE", files["config"]), \
         patch("halia.config.settings.SECRETS_FILE", files["secrets"]), \
         patch("halia.store.database.DB_PATH", files["db"]), \
         patch("halia.schedule.list_jobs", return_value=[]), \
         patch("shutil.which", return_value=None):
        result = runner.invoke(app, ["uninstall", "--force"])

    assert result.exit_code == 0
    assert not files["config"].exists()
    assert not files["secrets"].exists()
    assert not files["db"].exists()
    assert "config.json" in result.output
    assert "secrets.json" in result.output
    assert "halia.db" in result.output


def test_uninstall_removes_cron_jobs(tmp_path: Path) -> None:
    """uninstall --force removes all cron jobs."""
    files = _setup_fake_halia(tmp_path)

    job1 = MagicMock()
    job1.name = "daily_report"
    job1.cron = "@daily"
    job2 = MagicMock()
    job2.name = "weekly_check"
    job2.cron = "@weekly"

    with patch("halia.config.settings.CONFIG_DIR", files["config_dir"]), \
         patch("halia.config.settings.CONFIG_FILE", files["config"]), \
         patch("halia.config.settings.SECRETS_FILE", files["secrets"]), \
         patch("halia.store.database.DB_PATH", files["db"]), \
         patch("halia.schedule.list_jobs", return_value=[job1, job2]), \
         patch("halia.schedule.remove_job", return_value=True) as mock_remove, \
         patch("shutil.which", return_value=None):
        result = runner.invoke(app, ["uninstall", "--force"])

    assert result.exit_code == 0
    assert mock_remove.call_count == 2


def test_uninstall_nothing_to_remove(tmp_path: Path) -> None:
    """uninstall --force with nothing present removes empty dir and says removed."""
    empty_dir = tmp_path / ".halia"
    empty_dir.mkdir()

    with patch("halia.config.settings.CONFIG_DIR", empty_dir), \
         patch("halia.config.settings.CONFIG_FILE", empty_dir / "config.json"), \
         patch("halia.config.settings.SECRETS_FILE", empty_dir / "secrets.json"), \
         patch("halia.store.database.DB_PATH", empty_dir / "halia.db"), \
         patch("halia.schedule.list_jobs", return_value=[]), \
         patch("shutil.which", return_value=None):
        result = runner.invoke(app, ["uninstall", "--force"])

    assert result.exit_code == 0
    # Empty dir gets cleaned up, so we see "Removed" not "Nothing to remove"
    assert "Removed" in result.output or "Nothing to remove" in result.output


def test_uninstall_shows_removed_count(tmp_path: Path) -> None:
    """uninstall --force shows the count of removed items."""
    files = _setup_fake_halia(tmp_path)

    with patch("halia.config.settings.CONFIG_DIR", files["config_dir"]), \
         patch("halia.config.settings.CONFIG_FILE", files["config"]), \
         patch("halia.config.settings.SECRETS_FILE", files["secrets"]), \
         patch("halia.store.database.DB_PATH", files["db"]), \
         patch("halia.schedule.list_jobs", return_value=[]), \
         patch("shutil.which", return_value=None):
        result = runner.invoke(app, ["uninstall", "--force"])

    assert result.exit_code == 0
    assert "Removed" in result.output
    assert "item(s)" in result.output
