"""Permission floor.

A minimal, always-on denylist for read access — even "safe" read-only skills must
not exfiltrate secrets (ssh keys, .env, credentials). This is the sensitive-floor
pattern; the fuller leash (per-path allow/deny, approval gates for dangerous
actions like run_command) builds on top of it later.

Note: CUA (Computer Use Agent) operations bypass this guard because they operate
on the desktop UI (screenshots, clicks, typing), not the filesystem. CUA skills
do not call check_readable/check_writable. Audit logging is still enforced for
all CUA operations regardless of backend selection.
"""

from __future__ import annotations

from pathlib import Path


class PermissionDenied(RuntimeError):
    """Raised when access to a path is blocked by the permission floor."""


_SENSITIVE_DIRS = {".ssh", ".aws", ".gnupg", ".halia"}
_SENSITIVE_NAME_HINTS = (".env", "id_rsa", "id_ed25519", "credentials", "secret", ".pem")


def _check_floor(path: Path, action: str) -> None:
    resolved = path.expanduser()
    parts = {part.lower() for part in resolved.parts}
    if parts & _SENSITIVE_DIRS:
        raise PermissionDenied(f"{action} '{path}' is blocked (sensitive directory)")
    name = resolved.name.lower()
    if any(hint in name for hint in _SENSITIVE_NAME_HINTS):
        raise PermissionDenied(f"{action} '{path}' is blocked (sensitive file)")


def check_readable(path: Path) -> None:
    """Raise PermissionDenied if reading `path` is blocked by the floor."""
    _check_floor(path, "reading")


def check_writable(path: Path) -> None:
    """Raise PermissionDenied if writing `path` is blocked by the floor."""
    _check_floor(path, "writing")
