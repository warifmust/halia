"""disk_usage — read-only disk inspection (df + du).

The one safe, always-on way to answer "what's eating my disk" without shell
access. It runs `df` and `du` with FIXED, non-injectable arguments (no
shell=True, the path is passed as a plain argv element), so it needs no
approval and can ship in the default registry. Never drive Finder/CUA for this.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

# du can be slow on a huge tree; bound it so a runaway scan can't hang the loop.
_DU_TIMEOUT_SECS = 120
_DF_TIMEOUT_SECS = 20


def _coerce_int(value: Any, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(value))
    except (TypeError, ValueError):
        return default


def _human(n_bytes: int) -> str:
    """Bytes → compact human size (e.g. 82.4G)."""
    if n_bytes < 1024:
        return f"{n_bytes}B"
    value = float(n_bytes)
    for unit in ("K", "M", "G", "T", "P"):
        value /= 1024.0
        if value < 1024 or unit == "P":
            return f"{value:.1f}{unit}"
    return f"{value:.1f}P"


def _run(cmd: list[str], timeout: int, *, keep_stderr: bool) -> tuple[bool, str]:
    """Run a fixed command; return (ok, output). stderr is dropped unless kept."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout}s"
    except FileNotFoundError:
        return False, "command not found on this system"
    out = proc.stdout.strip()
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        return False, err or f"exit code {proc.returncode}"
    return True, out


class DiskUsage:
    name = "disk_usage"
    description = (
        "Report disk usage: the volume's free/used space (df) and the LARGEST "
        "directories/files under a path (du), ranked by size. Read-only and safe — "
        "no shell, no approval needed. Use this for 'what's taking up my disk' "
        "questions; NEVER drive Finder/CUA for them."
    )
    dangerous = False
    untrusted = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory to inspect (default: your home).",
            },
            "depth": {
                "type": "integer",
                "description": "How many levels deep to summarize (default 1).",
            },
            "top": {
                "type": "integer",
                "description": "Max entries to return (default 20).",
            },
        },
    }

    def run(self, args: dict[str, Any]) -> str:
        raw = args.get("path") if isinstance(args.get("path"), str) else None
        path = raw.strip() if raw and raw.strip() else "~"
        depth = _coerce_int(args.get("depth"), 1, 1)
        top = _coerce_int(args.get("top"), 20, 1)

        target = Path(path).expanduser()
        if not target.exists():
            return f"error: no such path: {target}"

        parts: list[str] = []

        # 1) Volume usage (df) — the free/used picture for the volume holding `target`.
        ok, df_out = _run(["df", "-h", str(target)], _DF_TIMEOUT_SECS, keep_stderr=True)
        if ok:
            parts.append(f"== volume (df -h) ==\n{df_out}")
        else:
            parts.append(f"df failed: {df_out}")

        # 2) Largest entries (du), ranked. Permission errors are noise — drop stderr.
        ok, du_out = _run(
            ["du", "-x", "-d", str(depth), "-k", str(target)],
            _DU_TIMEOUT_SECS,
            keep_stderr=False,
        )
        if ok:
            entries: list[tuple[int, str]] = []
            for line in du_out.splitlines():
                kb_str, sep, name = line.partition("\t")
                if not sep:
                    continue
                try:
                    kb = int(kb_str)
                except ValueError:
                    continue
                entries.append((kb, name))
            entries.sort(key=lambda e: e[0], reverse=True)
            shown = entries[:top]
            if shown:
                head = f"== largest {len(shown)} under {target} (du) =="
                body = "\n".join(f"{_human(kb * 1024):>8}  {name}" for kb, name in shown)
                parts.append(f"{head}\n{body}")
            else:
                parts.append("du returned no entries (directory may be empty).")
        else:
            parts.append(f"du failed: {du_out}")

        return "\n\n".join(parts)
