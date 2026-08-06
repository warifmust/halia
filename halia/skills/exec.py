"""The run_command skill — a *dangerous* capability.

Runs an arbitrary shell command. It is `dangerous = True`, so the agent loop will
not execute it without an approver's explicit yes (see core.agent). It is also
opt-in: not in the default registry unless commands are enabled.
"""

from __future__ import annotations

import subprocess
from typing import Any

_TIMEOUT_SECS = 60
_MAX_CHARS = 10_000


class RunCommand:
    name = "run_command"
    description = "Run a shell command and return its exit code, stdout, and stderr."
    dangerous = True
    untrusted = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"command": {"type": "string", "description": "The shell command to run."}},
        "required": ["command"],
    }

    def run(self, args: dict[str, Any]) -> str:
        raw = args.get("command")
        if not isinstance(raw, str) or not raw.strip():
            return "error: 'command' is required and must be a non-empty string"
        try:
            proc = subprocess.run(  # noqa: S602 — arbitrary shell is the point; gated by approval
                raw,
                shell=True,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECS,
            )
        except subprocess.TimeoutExpired:
            return f"error: command timed out after {_TIMEOUT_SECS}s"

        out = _truncate(proc.stdout)
        err = _truncate(proc.stderr)
        return f"exit_code: {proc.returncode}\nstdout:\n{out}\nstderr:\n{err}"


def _truncate(text: str) -> str:
    if len(text) > _MAX_CHARS:
        return text[:_MAX_CHARS] + "\n… [truncated]"
    return text
