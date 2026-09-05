"""Run the computer-use eval harness.

Usage:
  python -m halia.eval --provider openrouter --model qwen3.8-max --model claude-sonnet-5
  python -m halia.eval --provider anthropic --model claude-sonnet-5 --task saucedemo_add_6
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from halia.eval.harness import TaskOutcome, resolve_config, run_task
from halia.eval.tasks import ALL_TASKS


def _parse_models(raw: Sequence[str]) -> list[str]:
    models: list[str] = []
    for item in raw:
        models.extend(m.strip() for m in item.split(",") if m.strip())
    return models


def _render_report(outcomes: list[TaskOutcome]) -> str:
    if not outcomes:
        return "(no outcomes)"
    header = ("model", "task", "pass", "acts", "errs", "guards", "fabs", "secs")
    rows: list[tuple[str, ...]] = [header]
    for o in outcomes:
        rows.append((
            o.model, o.task,
            "PASS" if o.passed else "FAIL",
            str(o.actions), str(o.tool_errors), str(o.guard_events),
            str(len(o.fabrications)), f"{o.duration_s:.1f}",
        ))
    widths = [max(len(r[i]) for r in rows) for i in range(len(header))]
    lines = [
        "  ".join(cell.ljust(w) for cell, w in zip(r, widths, strict=False)).rstrip()
        for r in rows
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="halia.eval",
        description="A/B test models on deterministic computer-use tasks.",
    )
    parser.add_argument(
        "--model", action="append", required=True,
        help="model id to test (repeatable or comma-separated)",
    )
    parser.add_argument(
        "--provider", default="openrouter",
        help="provider whose API key/base URL to use (default: openrouter)",
    )
    parser.add_argument(
        "--task", action="append", default=None,
        help="task name to run (repeatable; default: all tasks)",
    )
    parser.add_argument("--max-iters", type=int, default=8)
    args = parser.parse_args(argv)

    models = _parse_models(args.model)
    if not models:
        parser.error("provide at least one --model")

    tasks = [t for t in ALL_TASKS if args.task is None or t.name in args.task]
    if not tasks:
        parser.error(f"no matching tasks; available: {[t.name for t in ALL_TASKS]}")

    outcomes: list[TaskOutcome] = []
    for model in models:
        config = resolve_config(args.provider, model)
        for task in tasks:
            print(f"\n▶ {model} · {task.name} — running…", file=sys.stderr)
            outcomes.append(run_task(task, config))

    print()
    print(_render_report(outcomes))
    for o in outcomes:
        print(f"\n— {o.model} · {o.task}: {o.details}")
        for fab in o.fabrications:
            print(f"  ⚠ fabrication: {fab}")
        if o.unverified:
            print(f"  ⚠ unverified figures: {', '.join(o.unverified)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
