"""Tests for halia doctor (read-only diagnostics)."""

from __future__ import annotations

from typing import Any

import pytest

from halia import doctor
from halia.config import settings
from halia.store import database, snapshots


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Any, monkeypatch: Any) -> None:
    # Redirect every path the checks read so nothing touches the real ~/.halia.
    home = tmp_path / "home"
    (home / ".halia").mkdir(parents=True)
    monkeypatch.setattr(settings, "CONFIG_DIR", home / ".halia")
    monkeypatch.setattr(settings, "CONFIG_FILE", home / ".halia" / "config.json")
    monkeypatch.setattr(settings, "SECRETS_FILE", home / ".halia" / "secrets.json")
    monkeypatch.setattr(database, "DB_PATH", home / ".halia" / "halia.db")
    monkeypatch.setattr(snapshots, "SNAPSHOTS_DIR", home / ".halia" / "snapshots")
    # No configured key by default → config check should fail cleanly, not explode.
    for var in ("HALIA_API_KEY", "OPENAI_API_KEY", "HALIA_PROVIDER"):
        monkeypatch.delenv(var, raising=False)


def _by_name(results: list[doctor.Check]) -> dict[str, doctor.Check]:
    return {c.name: c for c in results}


def test_run_checks_returns_all_checks() -> None:
    results = doctor.run_checks()
    names = {c.name for c in results}
    assert names == {
        "config", "secrets perms", "database", "fs floor",
        "egress floor", "scheduled jobs", "snapshots",
    }


def test_config_fails_without_key_but_does_not_raise() -> None:
    checks = _by_name(doctor.run_checks())
    assert checks["config"].status == doctor.FAIL
    assert "API key" in checks["config"].detail or "api key" in checks["config"].detail.lower()


def test_config_ok_with_env_key(monkeypatch: Any) -> None:
    monkeypatch.setenv("HALIA_API_KEY", "sk-test-xyz")
    checks = _by_name(doctor.run_checks())
    assert checks["config"].status == doctor.OK
    # The key value must never be echoed back.
    assert "sk-test-xyz" not in checks["config"].detail


def test_fs_floor_always_active() -> None:
    checks = _by_name(doctor.run_checks())
    assert checks["fs floor"].status == doctor.OK


def test_secrets_perms_warns_on_loose_mode() -> None:
    settings.SECRETS_FILE.write_text("{}")
    settings.SECRETS_FILE.chmod(0o644)  # group/other readable
    checks = _by_name(doctor.run_checks())
    assert checks["secrets perms"].status == doctor.WARN


def test_database_ok_when_absent_then_reports_when_present() -> None:
    # Absent → ok ("not created yet").
    assert _by_name(doctor.run_checks())["database"].status == doctor.OK
    # Create it via the real connect(), then doctor should read it as healthy.
    database.connect(database.DB_PATH).close()
    checks = _by_name(doctor.run_checks())
    assert checks["database"].status == doctor.OK
    assert "integrity ok" in checks["database"].detail


def test_database_fail_on_corrupt_file() -> None:
    database.DB_PATH.write_bytes(b"this is not a sqlite database")
    checks = _by_name(doctor.run_checks())
    assert checks["database"].status == doctor.FAIL


def test_egress_floor_reflects_state() -> None:
    from halia.permissions.network import set_allow_local

    try:
        set_allow_local(False)
        assert _by_name(doctor.run_checks())["egress floor"].status == doctor.OK
        set_allow_local(True)
        assert _by_name(doctor.run_checks())["egress floor"].status == doctor.WARN
    finally:
        set_allow_local(False)


def test_doctor_reads_only_no_db_created_when_absent() -> None:
    # Running the checks must NOT create the DB file.
    doctor.run_checks()
    assert not database.DB_PATH.exists()
