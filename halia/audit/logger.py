"""Structured JSON-line logging for halia runs.

Writes one JSON object per line (JSONL) to a file or stdout. Each event carries
a timestamp, event type, and relevant context. Designed for operational
observability — cron jobs, scheduled runs, debugging.

Usage:
    from halia.audit.logger import log_event
    log_event("tool_call", tool="http_request", duration_ms=320, status=200)

Env vars:
    HALIA_LOG — path to the log file (e.g. /var/log/halia.jsonl)
    HALIA_LOG_LEVEL — minimum severity: debug, info (default), warn, error
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

_log_file: Path | None = None
_log_level: str = "info"
_initialized = False

_LEVELS = {"debug": 0, "info": 1, "warn": 2, "error": 3}


def _ensure_init() -> None:
    global _log_file, _log_level, _initialized
    if _initialized:
        return
    _initialized = True
    env_path = os.environ.get("HALIA_LOG", "").strip()
    if env_path:
        _log_file = Path(env_path)
        _log_file.parent.mkdir(parents=True, exist_ok=True)
    _log_level = os.environ.get("HALIA_LOG_LEVEL", "info").lower()


def log_event(event: str, level: str = "info", **data: Any) -> None:
    """Write a structured JSON-line event.

    Args:
        event: Event type (e.g. "tool_call", "run_start", "run_end", "error").
        level: Severity (debug, info, warn, error).
        **data: Additional context fields (tool, duration_ms, tokens, etc.).
    """
    _ensure_init()
    if _LEVELS.get(level, 1) < _LEVELS.get(_log_level, 1):
        return
    if _log_file is None:
        return
    entry = {
        "ts": time.time(),
        "event": event,
        "level": level,
        **data,
    }
    try:
        with open(_log_file, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass  # never let logging break the run


def log_run_start(run_id: str, prompt: str, provider: str, model: str) -> None:
    """Log the start of a run."""
    log_event("run_start", run_id=run_id, prompt=prompt[:200], provider=provider, model=model)


def log_run_end(
    run_id: str,
    answer: str,
    steps_count: int,
    usage_total: int,
    corrections: int,
    duration_ms: float,
) -> None:
    """Log the end of a run."""
    log_event(
        "run_end",
        run_id=run_id,
        answer_preview=answer[:200],
        steps=steps_count,
        tokens=usage_total,
        corrections=corrections,
        duration_ms=round(duration_ms, 1),
    )


def log_tool_call(
    tool: str, arguments: str, duration_ms: float, status: str = "ok"
) -> None:
    """Log a single tool execution."""
    log_event(
        "tool_call",
        tool=tool,
        args_preview=arguments[:200],
        duration_ms=round(duration_ms, 1),
        status=status,
    )


def log_error(run_id: str, error: str) -> None:
    """Log an error during a run."""
    log_event("error", level="error", run_id=run_id, error=error)


def log_fallback(primary: str, fallback: str, error: str) -> None:
    """Log a provider fallback."""
    log_event("fallback", level="warn", primary=primary, fallback=fallback, error=error)
