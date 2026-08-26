"""Self-upgrade for halia: fetch the latest version from GitHub and reinstall via uv.

The ``halia upgrade`` command wires this module into the CLI. Keeping the logic
here — network fetch, version comparison, and the ``uv tool install`` re-run —
makes it unit-testable without hitting the CLI layer or a real network.
"""

from __future__ import annotations

import re
import shutil
import subprocess

import httpx

REPO_URL = "https://github.com/warifmust/halia.git"
REF = "main"
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
    try:
        result = subprocess.run(
            [uv, "tool", "install", "--force", target],
            capture_output=True,
            text=True,
            timeout=_INSTALL_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return (False, "timed out while re-installing halia via uv")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "uv tool install failed").strip()
        return (False, detail)
    detail = (result.stdout or result.stderr or "").strip()
    return (True, detail)
