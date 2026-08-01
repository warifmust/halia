"""Reconciliation — reconcile_csv.

The finance primitive: match two CSVs by a key column and report what agrees and
what doesn't (keys only in one file; exact value mismatches on a numeric column).
Deterministic and exact (Decimal), so a reconciliation is verifiable — the model
decides *what* to reconcile; the code decides *whether it ties out*.
"""

from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from halia.permissions.guard import check_readable

_LIST_CAP = 20


class _ColumnMissing(Exception):
    """A required column is not present in a file."""


def _to_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    cleaned = value.strip().lstrip("$").replace(",", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _values_differ(left: str | None, right: str | None) -> bool:
    left_num, right_num = _to_decimal(left), _to_decimal(right)
    if left_num is not None and right_num is not None:
        return left_num != right_num
    return (left or "").strip() != (right or "").strip()


def _load_csv(
    path: Path, key_col: str, value_col: str | None
) -> tuple[dict[str, str | None], int]:
    seen: dict[str, str | None] = {}
    duplicates = 0
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        reader = csv.DictReader(handle)
        columns = list(reader.fieldnames or [])
        if key_col not in columns:
            raise _ColumnMissing(
                f"error: key column '{key_col}' not in {path.name} (columns: {', '.join(columns)})"
            )
        if value_col is not None and value_col not in columns:
            raise _ColumnMissing(
                f"error: value column '{value_col}' not in {path.name} "
                f"(columns: {', '.join(columns)})"
            )
        for row in reader:
            key = (row.get(key_col) or "").strip()
            if not key:
                continue
            if key in seen:
                duplicates += 1
            seen[key] = row.get(value_col) if value_col is not None else None
    return seen, duplicates


class ReconcileCsv:
    name = "reconcile_csv"
    description = (
        "Reconcile two CSV files by a key column: report matched keys, keys only in "
        "each file, and (with a 'value' column) exact decimal value mismatches. Use "
        "this to check that two sources tie out."
    )
    dangerous = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "left": {"type": "string", "description": "Path to the first CSV."},
            "right": {"type": "string", "description": "Path to the second CSV."},
            "key": {"type": "string", "description": "Column to match rows on."},
            "value": {
                "type": "string",
                "description": "Optional numeric column to compare on matched keys.",
            },
        },
        "required": ["left", "right", "key"],
    }

    def run(self, args: dict[str, Any]) -> str:
        left_raw = args.get("left")
        right_raw = args.get("right")
        key = args.get("key")
        if not isinstance(left_raw, str) or not left_raw.strip():
            return "error: 'left' is required and must be a non-empty string"
        if not isinstance(right_raw, str) or not right_raw.strip():
            return "error: 'right' is required and must be a non-empty string"
        if not isinstance(key, str) or not key.strip():
            return "error: 'key' is required and must be a non-empty string"
        value = args.get("value")
        value_col = value if isinstance(value, str) and value.strip() else None

        left_path = Path(left_raw).expanduser()
        right_path = Path(right_raw).expanduser()
        check_readable(left_path)
        check_readable(right_path)
        if not left_path.is_file():
            return f"error: not a file: {left_path}"
        if not right_path.is_file():
            return f"error: not a file: {right_path}"

        try:
            left_map, left_dupes = _load_csv(left_path, key, value_col)
            right_map, right_dupes = _load_csv(right_path, key, value_col)
        except _ColumnMissing as exc:
            return str(exc)
        except (OSError, csv.Error) as exc:
            return f"error reading CSV: {exc}"

        left_keys, right_keys = set(left_map), set(right_map)
        matched = left_keys & right_keys
        only_left = sorted(left_keys - right_keys)
        only_right = sorted(right_keys - left_keys)

        def _capped(items: list[str]) -> str:
            shown = ", ".join(items[:_LIST_CAP])
            more = f" (+{len(items) - _LIST_CAP} more)" if len(items) > _LIST_CAP else ""
            return f" — {shown}{more}" if items else ""

        lines = [
            f"reconciliation on key '{key}': {left_path.name} vs {right_path.name}",
            f"matched keys: {len(matched)}",
            f"only in {left_path.name}: {len(only_left)}{_capped(only_left)}",
            f"only in {right_path.name}: {len(only_right)}{_capped(only_right)}",
        ]

        if value_col is not None:
            mismatches = [
                f"{k}: {left_map[k]} vs {right_map[k]}"
                for k in sorted(matched)
                if _values_differ(left_map[k], right_map[k])
            ]
            lines.append(f"value '{value_col}' mismatches: {len(mismatches)}")
            lines.extend(f"  {m}" for m in mismatches[:_LIST_CAP])

        if left_dupes or right_dupes:
            lines.append(
                f"note: duplicate keys — {left_path.name}: {left_dupes}, "
                f"{right_path.name}: {right_dupes}"
            )

        return "\n".join(lines)
