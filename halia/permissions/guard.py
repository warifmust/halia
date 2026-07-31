"""Permission floor.

A minimal, always-on denylist for read access — even "safe" read-only skills must
not exfiltrate secrets (ssh keys, .env, credentials). This is the sensitive-floor
pattern; the fuller leash (per-path allow/deny, approval gates for dangerous
actions like run_command) builds on top of it later.
"""

from __future__ import annotations

from pathlib import Path


class PermissionDenied(RuntimeError):
    """Raised when access to a path is blocked by the permission floor."""


_SENSITIVE_DIRS = {".ssh", ".aws", ".gnupg", ".halia"}
_SENSITIVE_NAME_HINTS = (".env", "id_rsa", "id_ed25519", "credentials", "secret", ".pem")


def check_readable(path: Path) -> None:
    """Raise PermissionDenied if `path` is in the sensitive floor."""
    resolved = path.expanduser()
    parts = {part.lower() for part in resolved.parts}
    if parts & _SENSITIVE_DIRS:
        raise PermissionDenied(f"reading '{path}' is blocked (sensitive directory)")
    name = resolved.name.lower()
    if any(hint in name for hint in _SENSITIVE_NAME_HINTS):
        raise PermissionDenied(f"reading '{path}' is blocked (sensitive file)")
