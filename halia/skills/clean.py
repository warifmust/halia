"""Data cleaning — clean_csv.

The transform-and-save gap SQL can't cover cleanly: standardise casing/dates, trim
whitespace, dedupe, fill/drop blanks, rename, remap categories — then write a CLEANED
CSV the analyst keeps. Cleaning is applied as an ORDERED list of deterministic
operations, and every step reports what it changed, so the transform is auditable, not
a black box (on-thesis: a trust product shows its work).
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any

from halia.permissions.guard import PermissionDenied, check_readable, check_writable

_MAX_ROWS = 100_000
# Tried in order. ISO first; day-first for slash/dash dates (pass an explicit `from`
# strptime format for anything ambiguous — that's deterministic).
_DATE_FORMATS = [
    "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%m-%d-%Y",
    "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%b %d %Y",
]


def _to_iso(value: str, explicit: str | None) -> tuple[str, bool]:
    text = value.strip()
    if not text:
        return value, False
    for fmt in ([explicit] if explicit else _DATE_FORMATS):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d"), True
        except ValueError:
            continue
    return value, False  # leave unparseable values unchanged


def _col_index(header: list[str], name: Any) -> int:
    return header.index(name) if isinstance(name, str) and name in header else -1


def _apply(op: dict[str, Any], header: list[str], rows: list[list[str]]) -> str:
    kind = op.get("op")
    col = op.get("column")
    idx = _col_index(header, col)
    if kind in ("trim", "lowercase", "uppercase", "titlecase", "fill_blank", "drop_missing",
                "replace", "standardize_date", "rename") and idx < 0:
        return f"{kind}: column '{col}' not found — skipped"

    if kind == "trim":
        n = sum(1 for r in rows if r[idx] != r[idx].strip())
        for r in rows:
            r[idx] = r[idx].strip()
        return f"trim({col}): {n} cells trimmed"
    if kind in ("lowercase", "uppercase", "titlecase"):
        fn = {"lowercase": str.lower, "uppercase": str.upper, "titlecase": str.title}[kind]
        n = 0
        for r in rows:
            new = fn(r[idx])
            n += r[idx] != new
            r[idx] = new
        return f"{kind}({col}): {n} cells changed"
    if kind == "fill_blank":
        value = str(op.get("value", ""))
        n = 0
        for r in rows:
            if r[idx].strip() == "":
                r[idx] = value
                n += 1
        return f"fill_blank({col}): {n} blanks filled"
    if kind == "drop_missing":
        before = len(rows)
        rows[:] = [r for r in rows if r[idx].strip() != ""]
        return f"drop_missing({col}): {before - len(rows)} rows removed"
    if kind == "replace":
        mapping = op.get("map")
        if not isinstance(mapping, dict):
            return "replace: 'map' must be an object of old->new"
        n = 0
        for r in rows:
            if r[idx] in mapping:
                r[idx] = str(mapping[r[idx]])
                n += 1
        return f"replace({col}): {n} cells remapped"
    if kind == "standardize_date":
        explicit = op.get("from")
        parsed = 0
        for r in rows:
            new, ok = _to_iso(r[idx], explicit if isinstance(explicit, str) else None)
            r[idx] = new
            parsed += ok
        return f"standardize_date({col}): {parsed}/{len(rows)} parsed to ISO"
    if kind == "rename":
        new_name = op.get("to")
        if not isinstance(new_name, str) or not new_name.strip():
            return "rename: 'to' is required"
        header[idx] = new_name
        return f"rename: '{col}' → '{new_name}'"
    if kind == "drop_duplicates":
        cols = op.get("columns")
        key_idx = (
            [i for i, h in enumerate(header) if h in cols]
            if isinstance(cols, list) and cols else list(range(len(header)))
        )
        seen: set[tuple[str, ...]] = set()
        before = len(rows)
        kept: list[list[str]] = []
        for r in rows:
            key = tuple(r[i] for i in key_idx)
            if key not in seen:
                seen.add(key)
                kept.append(r)
        rows[:] = kept
        return f"drop_duplicates: {before - len(rows)} rows removed"
    if kind == "drop_empty_rows":
        before = len(rows)
        rows[:] = [r for r in rows if any(c.strip() for c in r)]
        return f"drop_empty_rows: {before - len(rows)} rows removed"
    return f"unknown op '{kind}' — skipped"


class CleanCsv:
    name = "clean_csv"
    description = (
        "Clean a CSV via an ordered list of operations, saving a cleaned file; each step reports "
        "its change. Ops (object with 'op'): trim, lowercase/uppercase/titlecase, fill_blank, "
        "drop_missing, drop_duplicates, drop_empty_rows, replace, rename, standardize_date. Use "
        "query_data (SQL) for filtering/joining/aggregating."
    )
    dangerous = True
    untrusted = False  # writes a file
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "path": {"type": "string", "description": "Input CSV path."},
            "output": {"type": "string", "description": "Output (cleaned) CSV path."},
            "operations": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Ordered cleaning operations (see the description).",
            },
        },
        "required": ["path", "output", "operations"],
    }

    def run(self, args: dict[str, Any]) -> str:
        raw_in = args.get("path")
        raw_out = args.get("output")
        operations = args.get("operations")
        if not isinstance(raw_in, str) or not raw_in.strip():
            return "error: 'path' is required"
        if not isinstance(raw_out, str) or not raw_out.strip():
            return "error: 'output' is required"
        if not isinstance(operations, list) or not operations:
            return "error: 'operations' must be a non-empty array"

        src = Path(raw_in).expanduser()
        dst = Path(raw_out).expanduser()
        check_readable(src)
        try:
            check_writable(dst)
        except PermissionDenied as exc:
            return f"blocked: {exc}"
        if not src.is_file():
            return f"error: not a file: {src}"

        try:
            with src.open(newline="", encoding="utf-8", errors="replace") as handle:
                reader = csv.reader(handle)
                all_rows = list(reader)
        except (OSError, csv.Error) as exc:
            return f"error reading CSV: {exc}"
        if not all_rows:
            return "error: empty file"
        if len(all_rows) - 1 > _MAX_ROWS:
            return f"error: file too large (> {_MAX_ROWS} rows) to clean safely"

        header = list(all_rows[0])
        width = len(header)
        rows = [list(r) + [""] * (width - len(r)) for r in all_rows[1:]]
        before = len(rows)

        report: list[str] = []
        for op in operations:
            if not isinstance(op, dict):
                report.append("skipped a non-object operation")
                continue
            report.append(f"- {_apply(op, header, rows)}")

        try:
            with dst.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(header)
                writer.writerows(rows)
        except OSError as exc:
            return f"error writing {dst}: {exc}"

        summary = "\n".join(report)
        return f"cleaned {src.name} → {dst} (rows: {before} → {len(rows)})\n{summary}"
