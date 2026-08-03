"""Tests for OS-crontab scheduling (in-memory crontab via injected read/write)."""

import pytest

from halia.schedule import (
    ScheduleError,
    add_job,
    build_procedure_command,
    list_jobs,
    remove_job,
    validate_cron,
)


class FakeCron:
    """An in-memory stand-in for the user's crontab."""

    def __init__(self, lines: list[str] | None = None) -> None:
        self.lines = list(lines or [])

    def read(self) -> list[str]:
        return list(self.lines)

    def write(self, lines: list[str]) -> None:
        self.lines = list(lines)


def test_validate_cron_accepts_fields_and_aliases() -> None:
    validate_cron("0 9 * * *")
    validate_cron("@daily")
    with pytest.raises(ScheduleError):
        validate_cron("0 9 * *")  # only 4 fields
    with pytest.raises(ScheduleError):
        validate_cron("@sometimes")


def test_add_and_list_roundtrip() -> None:
    cron = FakeCron()
    add_job("nightly", "0 9 * * *", "halia procedure run x --quiet --yes",
            read=cron.read, write=cron.write)
    jobs = list_jobs(read=cron.read)
    assert len(jobs) == 1
    assert jobs[0].name == "nightly"
    assert jobs[0].cron == "0 9 * * *"
    assert "procedure run x" in jobs[0].command


def test_add_preserves_users_other_crontab_lines() -> None:
    cron = FakeCron(["0 0 * * * /usr/bin/backup.sh", "# my own note"])
    add_job("nightly", "@daily", "halia procedure run x", read=cron.read, write=cron.write)
    # the user's lines survive; exactly one halia-managed job is visible
    assert any("backup.sh" in line for line in cron.lines)
    assert len(list_jobs(read=cron.read)) == 1


def test_add_same_name_replaces() -> None:
    cron = FakeCron()
    add_job("job", "@daily", "halia procedure run a", read=cron.read, write=cron.write)
    add_job("job", "@hourly", "halia procedure run b", read=cron.read, write=cron.write)
    jobs = list_jobs(read=cron.read)
    assert len(jobs) == 1
    assert jobs[0].cron == "@hourly" and "run b" in jobs[0].command


def test_remove() -> None:
    cron = FakeCron()
    add_job("a", "@daily", "halia procedure run a", read=cron.read, write=cron.write)
    add_job("b", "@daily", "halia procedure run b", read=cron.read, write=cron.write)
    assert remove_job("a", read=cron.read, write=cron.write) is True
    assert remove_job("a", read=cron.read, write=cron.write) is False
    assert {j.name for j in list_jobs(read=cron.read)} == {"b"}


def test_invalid_cron_rejected_on_add() -> None:
    cron = FakeCron()
    with pytest.raises(ScheduleError):
        add_job("bad", "nonsense", "halia run x", read=cron.read, write=cron.write)
    assert cron.lines == []  # nothing written


def test_build_procedure_command() -> None:
    cmd = build_procedure_command("login-smoke", notify=True)
    assert "procedure run login-smoke" in cmd
    assert "--quiet --yes" in cmd
    assert "--notify" in cmd
    assert build_procedure_command("x", notify=False).endswith("--quiet --yes")
