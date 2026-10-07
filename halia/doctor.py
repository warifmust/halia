"""halia doctor — read-only diagnostics for a self-hosted install.

Runs a series of OFFLINE checks over the local install (config, secrets, database,
permission floor, scheduled jobs, snapshot store) and returns structured results.
Nothing here mutates state or touches the network — the database is opened read-only,
and no file is created. The CLI (`halia doctor`) renders the results.
"""

from __future__ import annotations

import sqlite3
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# Status is one of: "ok" (healthy), "warn" (works, but worth noting), "fail" (broken).
OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass
class Check:
    name: str
    status: str
    detail: str


def _config() -> Check:
    """Provider/model/base_url + an API key resolve (env → secrets → default)."""
    from halia.config.settings import ConfigError, load_config

    try:
        cfg = load_config()
    except ConfigError as exc:
        return Check("config", FAIL, str(exc))
    # Never print the key — presence only.
    return Check(
        "config",
        OK,
        f"provider={cfg.provider} model={cfg.model} base_url={cfg.base_url} · API key: present",
    )


def _secrets_perms() -> Check:
    """The secrets file, if present, must be owner-only (0600)."""
    from halia.config.settings import SECRETS_FILE

    if not SECRETS_FILE.exists():
        return Check("secrets perms", OK, "no secrets.json (using env vars or none)")
    mode = stat.S_IMODE(SECRETS_FILE.stat().st_mode)
    if mode & 0o077:
        return Check(
            "secrets perms",
            WARN,
            f"secrets.json is {oct(mode)} — group/other can read it; run `chmod 600` on it",
        )
    return Check("secrets perms", OK, f"secrets.json is {oct(mode)} (owner-only)")


def _database() -> Check:
    """The DB opens READ-ONLY and passes an integrity check (never created here)."""
    from halia.store.database import DB_PATH

    if not DB_PATH.exists():
        return Check("database", OK, "not created yet (first run will create it)")
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {r[0] for r in rows}
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return Check("database", FAIL, f"cannot open {DB_PATH}: {exc}")
    if not integrity or integrity[0] != "ok":
        return Check("database", FAIL, f"integrity check failed: {integrity}")
    expected = {"runs", "sessions", "snapshots"}
    missing = expected - tables
    if missing:
        return Check("database", WARN, f"missing tables {sorted(missing)} (older DB?)")
    return Check("database", OK, f"integrity ok · {len(tables)} tables")


def _fs_floor() -> Check:
    """The sensitive-file guard actually blocks a known-sensitive path."""
    from halia.permissions.guard import PermissionDenied, check_readable

    probe = Path.home() / ".ssh" / "id_rsa"
    try:
        check_readable(probe)
    except PermissionDenied:
        return Check("fs floor", OK, "sensitive-path guard active (.ssh/.env/keys blocked)")
    return Check("fs floor", FAIL, "sensitive-path guard did NOT block ~/.ssh/id_rsa")


def _egress_floor() -> Check:
    """Report the network egress floor state (SSRF protection)."""
    from halia.permissions.network import allow_local_enabled

    if allow_local_enabled():
        return Check(
            "egress floor",
            WARN,
            "local/loopback egress is ON (dev mode) — turn off with `/local off` when done",
        )
    return Check("egress floor", OK, "egress floor active (localhost/LAN blocked)")


def _cron() -> Check:
    """Scheduled jobs halia manages in the user's crontab."""
    try:
        from halia.schedule import list_jobs

        jobs = list_jobs()
    except Exception as exc:  # noqa: BLE001 — crontab may be unavailable on this host
        return Check("scheduled jobs", WARN, f"could not read crontab: {exc}")
    if not jobs:
        return Check("scheduled jobs", OK, "none")
    names = ", ".join(j.name for j in jobs)
    return Check("scheduled jobs", OK, f"{len(jobs)} job(s): {names}")


def _cua_driver() -> Check:
    """cua-driver presence and version — the optional computer-use backend.

    CUA is optional, so a missing driver is fine. An installed one outside halia's
    supported range is not: 0.34.0+ requires a `cursor_motion` field halia does not
    pass, which breaks every cua_* tool at session start.
    """
    from halia.computer.cua_backend import (
        CUA_DRIVER_RANGE,
        CUA_DRIVER_SPEC,
        cua_driver_supported,
        runtime_cua_driver_version,
    )

    version = runtime_cua_driver_version()
    if version is None:
        return Check("cua-driver", OK, "not installed (optional backend)")
    if not cua_driver_supported(version):
        return Check(
            "cua-driver",
            FAIL,
            f"{version} is outside the supported range "
            f"({CUA_DRIVER_RANGE}) — run `halia setup --cua` to pin it back",
        )
    pinned = CUA_DRIVER_SPEC.lstrip("=")
    if version != pinned:
        # In band, so it works — but it is not the build halia is validated against.
        return Check(
            "cua-driver",
            WARN,
            f"{version} is in the supported range ({CUA_DRIVER_RANGE}) but halia is "
            f"validated against {pinned}; `halia setup --cua` pins that version",
        )
    return Check("cua-driver", OK, f"{version} (pinned {CUA_DRIVER_SPEC})")


def _extras() -> Check:
    """Extras halia recorded as needed that are no longer installed.

    `uv tool install --force` rebuilds a tool venv from its own command line, so a
    stripped extra is exactly the failure that reads as "`mcp` package not
    installed" or a `cursor_motion` TypeError later on.
    """
    from halia.upgrade import declared_extras, missing_declared_extras, requirement_name

    declared = declared_extras()
    if not declared:
        return Check("extras", OK, "none recorded")
    missing = [requirement_name(spec) for spec in missing_declared_extras()]
    if missing:
        return Check(
            "extras",
            FAIL,
            f"{', '.join(missing)} recorded but not installed — "
            f"run `halia upgrade` to restore",
        )
    return Check("extras", OK, ", ".join(requirement_name(spec) for spec in declared))


def _snapshots() -> Check:
    """File-write snapshot store (undo). Counted from disk — no DB needed."""
    from halia.store.snapshots import SNAPSHOTS_DIR

    if not SNAPSHOTS_DIR.exists():
        return Check("snapshots", OK, "no snapshots yet")
    files = [p for p in SNAPSHOTS_DIR.iterdir() if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    return Check("snapshots", OK, f"{len(files)} snapshot(s), {total / 1024:.0f} KB")


_CHECKS: tuple[Callable[[], Check], ...] = (
    _config,
    _secrets_perms,
    _database,
    _fs_floor,
    _egress_floor,
    _cron,
    _snapshots,
    _extras,
    _cua_driver,
)


def run_checks() -> list[Check]:
    """Run every diagnostic and return the results in order."""
    return [check() for check in _CHECKS]
