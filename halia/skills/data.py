"""Structured-data skills — read tabular data.

`read_csv` *surfaces* a CSV's structure (columns, row count, a sample) so the
model can reason about it. It deliberately does NOT compute over the data — that
stays deterministic (calculate, and a future aggregate skill), never the LLM
summing rows in its head.
"""

from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from halia.permissions.guard import check_readable

_MAX_ROWS = 100_000  # cap the scan so a huge file can't exhaust memory
_DEFAULT_SAMPLE = 10


class ReadCsv:
    name = "read_csv"
    description = "Read a CSV file: return its columns, row count, and a sample of rows."
    dangerous = False
    untrusted = True
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "path": {"type": "string", "description": "Path to the CSV file."},
            "sample_rows": {
                "type": "integer",
                "description": "How many data rows to sample (default 10).",
            },
        },
        "required": ["path"],
    }

    def run(self, args: dict[str, Any]) -> str:
        raw = args.get("path")
        if not isinstance(raw, str) or not raw.strip():
            return "error: 'path' is required and must be a non-empty string"
        path = Path(raw).expanduser()
        check_readable(path)  # raises PermissionDenied for sensitive paths
        if not path.is_file():
            return f"error: not a file: {path}"

        sample_n = args.get("sample_rows", _DEFAULT_SAMPLE)
        if not isinstance(sample_n, int) or sample_n <= 0:
            sample_n = _DEFAULT_SAMPLE

        header: list[str] | None = None
        sample: list[list[str]] = []
        count = 0
        truncated = False
        try:
            with path.open(newline="", encoding="utf-8", errors="replace") as handle:
                reader = csv.reader(handle)
                for i, row in enumerate(reader):
                    if i == 0:
                        header = row
                        continue
                    count += 1
                    if len(sample) < sample_n:
                        sample.append(row)
                    if count >= _MAX_ROWS:
                        truncated = True
                        break
        except (OSError, csv.Error) as exc:
            return f"error reading CSV: {exc}"

        if header is None:
            return "(empty file)"

        lines = [
            f"columns ({len(header)}): {', '.join(header)}",
            f"data rows: {count}{'+' if truncated else ''}",
            "sample:",
            " | ".join(header),
        ]
        lines.extend(" | ".join(row) for row in sample)
        return "\n".join(lines)


_AGG_OPS = ("sum", "mean", "min", "max", "count")


def _decimal_sum(values: list[Decimal]) -> Decimal:
    total = Decimal(0)
    for value in values:
        total += value
    return total


class AggregateCsv:
    name = "aggregate_csv"
    description = (
        "Compute an EXACT aggregate over a numeric CSV column, in code over ALL rows "
        "(sum, mean, min, max, count). Uses exact decimal math — use this to total or "
        "average a column instead of summing sampled rows yourself."
    )
    dangerous = False
    untrusted = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "path": {"type": "string", "description": "Path to the CSV file."},
            "column": {"type": "string", "description": "The numeric column to aggregate."},
            "operation": {
                "type": "string",
                "enum": list(_AGG_OPS),
                "description": "The aggregation to compute.",
            },
        },
        "required": ["path", "column", "operation"],
    }

    def run(self, args: dict[str, Any]) -> str:
        raw_path = args.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return "error: 'path' is required and must be a non-empty string"
        column = args.get("column")
        if not isinstance(column, str) or not column.strip():
            return "error: 'column' is required and must be a non-empty string"
        operation = args.get("operation")
        if operation not in _AGG_OPS:
            return f"error: 'operation' must be one of: {', '.join(_AGG_OPS)}"

        path = Path(raw_path).expanduser()
        check_readable(path)
        if not path.is_file():
            return f"error: not a file: {path}"

        values: list[Decimal] = []
        skipped = 0
        try:
            with path.open(newline="", encoding="utf-8", errors="replace") as handle:
                reader = csv.DictReader(handle)
                if not reader.fieldnames or column not in reader.fieldnames:
                    cols = ", ".join(reader.fieldnames or [])
                    return f"error: column '{column}' not found. Columns: {cols}"
                for i, row in enumerate(reader):
                    if i >= _MAX_ROWS:
                        return f"error: file too large (> {_MAX_ROWS} rows) to aggregate safely"
                    cell = (row.get(column) or "").strip().lstrip("$").replace(",", "")
                    try:
                        values.append(Decimal(cell))
                    except InvalidOperation:
                        skipped += 1
        except (OSError, csv.Error) as exc:
            return f"error reading CSV: {exc}"

        if operation == "count":
            return f"count({column}) = {len(values)} numeric values"
        if not values:
            return f"error: no numeric values found in column '{column}'"
        if operation == "sum":
            result = _decimal_sum(values)
        elif operation == "mean":
            result = _decimal_sum(values) / Decimal(len(values))
        elif operation == "min":
            result = min(values)
        else:  # max
            result = max(values)

        note = f" ({skipped} non-numeric skipped)" if skipped else ""
        return f"{operation}({column}) = {result} over {len(values)} values{note}"


def _aggregate(op: str, values: list[Decimal]) -> Decimal:
    if op == "sum":
        return _decimal_sum(values)
    if op == "mean":
        return _decimal_sum(values) / Decimal(len(values))
    if op == "min":
        return min(values)
    return max(values)


_DEFAULT_GROUP_LIMIT = 50


class GroupByCsv:
    name = "group_by"
    description = (
        "Group a CSV by a key column and aggregate a value column per group "
        "(sum/mean/min/max, or count of rows), in exact decimal math over ALL rows. "
        "The data-analysis workhorse — use this instead of eyeballing groups. Results are "
        "sorted by the aggregate, largest first."
    )
    dangerous = False
    untrusted = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "path": {"type": "string", "description": "Path to the CSV file."},
            "group_by": {"type": "string", "description": "The key column to group by."},
            "operation": {
                "type": "string",
                "enum": list(_AGG_OPS),
                "description": "Aggregation per group. 'count' needs no value column.",
            },
            "value": {
                "type": "string",
                "description": "Numeric column to aggregate (not needed for count).",
            },
            "limit": {"type": "integer", "description": "Max groups to show (default 50)."},
        },
        "required": ["path", "group_by", "operation"],
    }

    def run(self, args: dict[str, Any]) -> str:
        raw_path = args.get("path")
        key_col = args.get("group_by")
        operation = args.get("operation")
        value_col = args.get("value")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return "error: 'path' is required"
        if not isinstance(key_col, str) or not key_col.strip():
            return "error: 'group_by' is required"
        if operation not in _AGG_OPS:
            return f"error: 'operation' must be one of: {', '.join(_AGG_OPS)}"
        if operation != "count" and (not isinstance(value_col, str) or not value_col.strip()):
            return "error: 'value' (numeric column) is required for that operation"

        limit = args.get("limit", _DEFAULT_GROUP_LIMIT)
        if not isinstance(limit, int) or limit <= 0:
            limit = _DEFAULT_GROUP_LIMIT

        path = Path(raw_path).expanduser()
        check_readable(path)
        if not path.is_file():
            return f"error: not a file: {path}"

        groups: dict[str, list[Decimal]] = {}
        counts: dict[str, int] = {}
        try:
            with path.open(newline="", encoding="utf-8", errors="replace") as handle:
                reader = csv.DictReader(handle)
                fields = reader.fieldnames or []
                if key_col not in fields:
                    return f"error: column '{key_col}' not found. Columns: {', '.join(fields)}"
                if operation != "count" and value_col not in fields:
                    return f"error: column '{value_col}' not found. Columns: {', '.join(fields)}"
                for i, row in enumerate(reader):
                    if i >= _MAX_ROWS:
                        return f"error: file too large (> {_MAX_ROWS} rows) to group safely"
                    key = (row.get(key_col) or "").strip() or "(blank)"
                    counts[key] = counts.get(key, 0) + 1
                    if operation != "count":
                        cell = (row.get(value_col) or "").strip().lstrip("$").replace(",", "")
                        try:
                            groups.setdefault(key, []).append(Decimal(cell))
                        except InvalidOperation:
                            pass
        except (OSError, csv.Error) as exc:
            return f"error reading CSV: {exc}"

        if operation == "count":
            results = [(key, Decimal(n)) for key, n in counts.items()]
            header = f"group_by '{key_col}' → count"
        else:
            results = [(key, _aggregate(operation, vals)) for key, vals in groups.items() if vals]
            header = f"group_by '{key_col}' → {operation}({value_col})"
        if not results:
            return "no groups with numeric values found."

        results.sort(key=lambda kv: kv[1], reverse=True)
        shown = results[:limit]
        more = f" (top {limit} of {len(results)} shown)" if len(results) > limit else ""
        lines = [f"{header} — {len(results)} groups{more}:"]
        lines.extend(f"{key} | {format(val, 'f')}" for key, val in shown)
        return "\n".join(lines)
