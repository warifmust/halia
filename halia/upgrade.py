"""Self-upgrade for halia: fetch the latest version from GitHub and reinstall via uv.

The ``halia upgrade`` command wires this module into the CLI. Keeping the logic
here — network fetch, version comparison, and the ``uv tool install`` re-run —
makes it unit-testable without hitting the CLI layer or a real network.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Iterable, Sequence

import httpx

REPO_URL = "https://github.com/warifmust/halia.git"
REF = "main"
# Config key holding the requirement specs halia's env must have. See declared_extras.
EXTRAS_KEY = "tool_extras"
# Single source of truth for the latest version: the tracked __version__ file.
RAW_VERSION_URL = (
    "https://raw.githubusercontent.com/warifmust/halia/main/halia/__init__.py"
)
_INSTALL_TIMEOUT = 600  # seconds — uv re-clones and rebuilds the tool venv

_VERSION_RE = re.compile(r'__version__\s*=\s*["\']([^"\']+)["\']')


def parse_version(version: str) -> tuple[int, ...]:
    """Parse a MAJOR.SPRINT.HOTFIX version string into a comparable tuple."""
    parts: list[int] = []
    for piece in version.strip().split("."):
        match = re.match(r"\d+", piece)
        if match is None:
            break
        parts.append(int(match.group(0)))
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    """Return True when `latest` sorts strictly above `current`."""
    return parse_version(latest) > parse_version(current)


def fetch_latest_version(client: httpx.Client | None = None) -> str:
    """Fetch and parse ``__version__`` from the GitHub main branch."""
    http = (
        client
        if client is not None
        else httpx.Client(timeout=15.0, follow_redirects=True)
    )
    try:
        resp = http.get(RAW_VERSION_URL)
        resp.raise_for_status()
        text = resp.text
    finally:
        if client is None:
            http.close()
    match = _VERSION_RE.search(text)
    if match is None:
        raise RuntimeError(f"could not parse __version__ from {RAW_VERSION_URL}")
    return match.group(1)


def _tool_dir() -> str | None:
    """uv's tool directory, or None when uv is missing or the query fails."""
    uv = shutil.which("uv")
    if not uv:
        return None
    try:
        result = subprocess.run(
            [uv, "tool", "dir"], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def installed_extra_requirements(tool_dir: str | None = None) -> list[str]:
    """Requirements recorded in halia's tool receipt beyond halia itself.

    ``uv tool install --force`` rebuilds the venv from the requirement list it is
    handed, so a plain re-install silently drops anything added out-of-band — the
    ``--with mcp`` extra, for instance. Re-applying them keeps an upgrade from
    quietly disabling a backend.

    Plain registry specs only: anything exotic (git/url/path/editable) is skipped
    rather than risk emitting an install command uv would reject.
    """
    import tomllib
    from pathlib import Path

    directory = tool_dir if tool_dir is not None else _tool_dir()
    if directory is None:
        return []
    receipt = Path(directory) / "halia" / "uv-receipt.toml"
    try:
        data = tomllib.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    requirements = data.get("tool", {}).get("requirements")
    if not isinstance(requirements, list):
        return []
    specs: list[str] = []
    for entry in requirements:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            continue
        # halia itself is the install target, not an extra to pass with --with.
        if name.split("[")[0] == "halia":
            continue
        if any(key in entry for key in ("git", "url", "path", "editable")):
            continue
        specifier = entry.get("specifier")
        specs.append(f"{name}{specifier}" if isinstance(specifier, str) else name)
    return specs


def requirement_name(spec: str) -> str:
    """The package name at the front of a requirement spec ('mcp>=1' → 'mcp')."""
    for index, char in enumerate(spec):
        if char in "<>=!~[; ":
            return spec[:index]
    return spec


def declared_extras() -> list[str]:
    """Requirements halia recorded as needed in its own config.

    Durable by design. ``uv tool install --force`` rebuilds the tool venv from the
    requirement list it is handed *and* rewrites the receipt to match, so the
    receipt cannot be the source of truth for what an environment needs: one bare
    ``uv tool install --force`` erases the only record of ``mcp`` and
    ``cua-driver``, and no later upgrade can tell they were ever wanted.
    ``~/.halia/config.json`` is written by halia alone and survives that.
    """
    from halia.config.settings import read_config

    try:
        data = read_config()
    except (OSError, ValueError):
        return []
    value = data.get(EXTRAS_KEY)
    if not isinstance(value, list):
        return []
    return [spec.strip() for spec in value if isinstance(spec, str) and spec.strip()]


def remember_extras(specs: Iterable[str]) -> None:
    """Merge requirement specs into halia's durable config record.

    Best-effort: a config halia cannot write must never fail an install that
    already succeeded.
    """
    from halia.config.settings import read_config, write_config

    merged: dict[str, str] = {}
    for spec in [*specs, *declared_extras()]:
        name = requirement_name(spec)
        if not name:
            continue
        existing = merged.get(name)
        # A pinned spec supersedes a bare name for the same package.
        if existing is None or (existing == name and spec != name):
            merged[name] = spec
    try:
        data = read_config()
        data[EXTRAS_KEY] = [merged[name] for name in sorted(merged)]
        write_config(data)
    except (OSError, ValueError):
        return


def install_requirements(
    specs: Sequence[str], *, timeout: int = 300
) -> tuple[bool, str]:
    """Install requirement specs into the interpreter halia is running under.

    Uses ``uv pip install --python sys.executable`` (falling back to pip) rather
    than ``uv tool install``: that works from a tool install, a venv, or plain
    pip, and — crucially — it leaves the tool receipt alone, so an on-demand extra
    cannot disturb the install command halia itself was created with.
    """
    import shutil
    import sys

    uv = shutil.which("uv")
    if uv:
        command = [uv, "pip", "install", "--python", sys.executable, *specs]
    else:
        command = [sys.executable, "-m", "pip", "install", *specs]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return (False, "timed out")
    except OSError as exc:
        return (False, str(exc))
    if result.returncode != 0:
        return (False, (result.stderr or result.stdout or "install failed").strip())
    return (True, "")


def add_extras(specs: Sequence[str], *, timeout: int = 300) -> tuple[bool, str]:
    """Install requirement specs and record them so reinstalls keep them.

    Recording is the point: the environment alone cannot remember, because
    ``uv tool install --force`` rebuilds it from the command line it is given.
    """
    ok, detail = install_requirements(specs, timeout=timeout)
    if ok:
        remember_extras(specs)
    return (ok, detail)


def is_installed(distribution: str) -> bool:
    """Whether a distribution is installed in this environment."""
    import importlib.metadata as metadata

    try:
        metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return False
    return True


def missing_declared_extras() -> list[str]:
    """Recorded specs that are no longer installed — a stripped environment.

    `uv tool install --force` can drop these without changing halia's own version,
    which is why an up-to-date version is not the same as a healthy environment.
    """
    return [
        spec
        for spec in declared_extras()
        if requirement_name(spec) and not is_installed(requirement_name(spec))
    ]


def repair_extras() -> tuple[bool, str]:
    """Install recorded extras that went missing, without reinstalling halia.

    `halia upgrade` does nothing when the version already matches, so without this
    a stripped environment would stay stripped until the next release.
    """
    missing = missing_declared_extras()
    if not missing:
        return (True, "nothing missing")
    ok, detail = install_requirements(missing)
    if not ok:
        return (False, detail)
    return (True, ", ".join(requirement_name(spec) for spec in missing))


def upgrade_with_requirements() -> list[str]:
    """The ``--with`` specs an upgrade must re-apply to keep CUA and MCP working.

    The cua-driver pin is re-derived from halia's validated spec rather than
    trusted from the record, so an upgrade can never carry an unsupported driver
    forward — a 0.34.0+ driver makes every cua_* tool fail at session start.
    """
    from halia.computer.cua_backend import (
        CUA_DRIVER_SPEC,
        runtime_cua_driver_version,
    )

    by_name: dict[str, str] = {}
    # halia's own record first, then the receipt: the record outlives the receipt.
    for spec in [*declared_extras(), *installed_extra_requirements()]:
        name = requirement_name(spec)
        if name and name not in by_name:
            by_name[name] = spec
    # Re-apply the driver when CUA is installed *or* still recorded, so an upgrade
    # also repairs an environment where it was already dropped. A driver the user
    # removed from both the config and the receipt stays removed.
    if runtime_cua_driver_version() is not None and "cua-driver" not in by_name:
        by_name["cua-driver"] = "cua-driver"
    if "cua-driver" in by_name:
        by_name["cua-driver"] = f"cua-driver{CUA_DRIVER_SPEC}"
    return [by_name[name] for name in sorted(by_name)]


def perform_upgrade() -> tuple[bool, str]:
    """Reinstall halia from GitHub main via ``uv tool install --force``.

    Returns ``(ok, detail)`` where detail is a user-facing message: uv's stdout
    on success, or the error text on failure.
    """
    uv = shutil.which("uv")
    if not uv:
        return (
            False,
            "uv not found on PATH — install uv (https://docs.astral.sh/uv/) or "
            "run the install script once to bootstrap it.",
        )
    target = f"git+{REPO_URL}@{REF}"
    command = [uv, "tool", "install", "--force", target]
    # --force rebuilds the venv from these requirements alone, so anything not
    # re-applied here is removed.
    extras = upgrade_with_requirements()
    for spec in extras:
        command += ["--with", spec]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=_INSTALL_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return (False, "timed out while re-installing halia via uv")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "uv tool install failed").strip()
        return (False, detail)
    # Record what this install carried. The receipt uv just wrote can be erased by
    # the next bare `uv tool install --force`; the config cannot.
    remember_extras(extras)
    detail = (result.stdout or result.stderr or "").strip()
    return (True, detail)
