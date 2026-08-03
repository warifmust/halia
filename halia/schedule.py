"""Scheduling via the OS crontab — no daemon.

halia doesn't run a scheduler; it manages entries in the user's crontab that invoke the
headless CLI (`halia procedure run … --quiet --yes`). The OS cron daemon does the timing.
Each managed line carries a trailing `# halia:<name>` marker so we can list and remove
only our own entries and never disturb the user's other cron jobs.

Cron runs in the machine's local timezone (per-agent timezone is a future refinement).
`read`/`write` are injectable so the add/list/remove logic is unit-testable without
touching the real crontab.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass

MARKER = "# halia:"
_CRON_ALIASES = {"@reboot", "@yearly", "@annually", "@monthly", "@weekly", "@daily", "@hourly"}


@dataclass(frozen=True)
class Job:
    """One managed cron entry."""

    name: str
    cron: str
    command: str


class ScheduleError(RuntimeError):
    """Raised when a crontab operation fails or input is invalid."""


CronReader = Callable[[], list[str]]
CronWriter = Callable[[list[str]], None]


def _read_crontab() -> list[str]:
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if result.returncode != 0:
        return []  # no crontab yet (or none for this user)
    return result.stdout.splitlines()


def _write_crontab(lines: list[str]) -> None:
    content = "\n".join(line for line in lines if line.strip()) + "\n"
    result = subprocess.run(["crontab", "-"], input=content, text=True, capture_output=True)
    if result.returncode != 0:
        raise ScheduleError(f"could not write crontab: {result.stderr.strip()}")


def halia_bin() -> str:
    """Absolute path to the halia executable (cron has a minimal PATH)."""
    return shutil.which("halia") or sys.argv[0]


def validate_cron(expr: str) -> None:
    """Raise ScheduleError unless `expr` is a 5-field cron spec or a known @alias."""
    expr = expr.strip()
    if expr in _CRON_ALIASES:
        return
    if len(expr.split()) != 5:
        raise ScheduleError(
            f"invalid cron '{expr}' — need 5 fields (min hour day month weekday) "
            "or an @alias like @daily"
        )


def _format(job: Job) -> str:
    return f"{job.cron} {job.command} {MARKER}{job.name}"


def _parse(line: str) -> Job | None:
    idx = line.rfind(MARKER)
    if idx == -1:
        return None
    name = line[idx + len(MARKER):].strip()
    rest = line[:idx].strip()
    parts = rest.split()
    if not name or not parts:
        return None
    if parts[0] in _CRON_ALIASES:
        cron, command = parts[0], " ".join(parts[1:])
    else:
        cron, command = " ".join(parts[:5]), " ".join(parts[5:])
    return Job(name=name, cron=cron, command=command)


def list_jobs(read: CronReader = _read_crontab) -> list[Job]:
    """All halia-managed cron jobs (ignores the user's other entries)."""
    jobs = [_parse(line) for line in read()]
    return [j for j in jobs if j is not None]


def add_job(
    name: str,
    cron: str,
    command: str,
    read: CronReader = _read_crontab,
    write: CronWriter = _write_crontab,
) -> Job:
    """Add or replace a managed job (same name → replaced). Returns the stored Job."""
    if not name.strip():
        raise ScheduleError("a job name is required")
    if MARKER in name or "\n" in name or "\n" in command:
        raise ScheduleError("name/command must not contain newlines or the halia marker")
    validate_cron(cron)
    job = Job(name=name.strip(), cron=cron.strip(), command=command.strip())
    # keep every line except a prior entry with the same name
    kept = [line for line in read() if (_parse(line) is None or _parse(line).name != job.name)]  # type: ignore[union-attr]
    kept.append(_format(job))
    write(kept)
    return job


def remove_job(
    name: str, read: CronReader = _read_crontab, write: CronWriter = _write_crontab
) -> bool:
    """Remove a managed job by name. True if it existed."""
    lines = read()
    kept = [line for line in lines if (_parse(line) is None or _parse(line).name != name)]  # type: ignore[union-attr]
    if len(kept) == len(lines):
        return False
    write(kept)
    return True


def build_procedure_command(procedure: str, notify: bool) -> str:
    """The headless command a scheduled procedure run invokes."""
    cmd = f"{halia_bin()} procedure run {procedure} --quiet --yes"
    if notify:
        cmd += " --notify"
    return cmd
