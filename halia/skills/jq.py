"""jq_query — deterministic JSON data extraction.

Instead of reading entire JSON files and having the model parse them, use
jq_query to extract specific fields, filter arrays, or compute aggregates.
Deterministic, cheap, and safe — no shell access needed.

Uses Python's json + a small jmespath/jq-like parser (dot-notation, array
index/iteration, pipes, and simple filters) built with plain string parsing —
no eval, no shell, no subprocess, no external dependencies.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from halia.permissions.guard import check_readable

_MAX_OUTPUT = 5000  # chars


class JqQuery:
    name = "jq_query"
    description = (
        "Extract, filter, or transform data from a JSON file. Examples:\n"
        "  '.name' — simple field\n"
        "  '.config.host' — nested field\n"
        "  '.users[].name' — iterate array, extract field\n"
        "  '.users[0].name' — array index\n"
        "  '.users[?age > 30].name' — filter + extract field\n"
        "  '.users | length' — count items\n"
        "  '.users | map(select(.active == true))' — filter array\n"
        "  '.config | keys' — object keys, sorted (like jq)\n"
        "  '.config | keys_unsorted' — object keys in insertion order\n"
        "  '.config | values' — object values (insertion order)"
    )
    dangerous = False
    untrusted = True  # reads user-supplied file content
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "path": {"type": "string", "description": "Path to the JSON file."},
            "query": {
                "type": "string",
                "description": (
                    "JMESPath-like query. Examples: '.name', '.items[0].price', "
                    "'.users[].email', '.config | length', "
                    "'.users[?age > 30].name'"
                ),
            },
            "max_chars": {
                "type": "integer",
                "description": f"Max characters in output (default {_MAX_OUTPUT}).",
            },
        },
        "required": ["path", "query"],
    }

    def run(self, args: dict[str, Any]) -> str:
        raw_path = args.get("path")
        query = args.get("query")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return "error: 'path' is required and must be a non-empty string"
        if not isinstance(query, str) or not query.strip():
            return "error: 'query' is required and must be a non-empty string"

        path = Path(raw_path).expanduser()
        check_readable(path)
        if not path.is_file():
            return f"error: not a file: {path}"

        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            return f"error: invalid JSON in {path.name}: {exc}"
        except OSError as exc:
            return f"error reading {path}: {exc}"

        try:
            result = _jq_query(data, query)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            return f"error: query failed: {exc}"

        max_chars = args.get("max_chars", _MAX_OUTPUT)
        if not isinstance(max_chars, int) or max_chars <= 0:
            max_chars = _MAX_OUTPUT

        output = json.dumps(result, ensure_ascii=False, indent=2, default=str)
        if len(output) > max_chars:
            output = output[:max_chars] + "\n… (truncated)"
        return output


def _jq_query(data: Any, query: str) -> Any:
    """Execute a simplified jq/JMESPath-like query on data.

    Supported syntax:
        .field          — dot-notation field access
        .field.sub      — nested access
        [0]             — array index
        []              — flatten/iterate
        .field[]        — iterate array field
        | length        — pipe to length
        | keys          — object keys, SORTED (matches jq)
        | keys_unsorted — object keys in insertion order
        | values        — object values (insertion order)
        | type          — pipe to type name
        [?expr]         — filter (simple comparisons only)
        .field == val   — equality check in filter
        .field > val    — greater-than in filter
        .field < val    — less-than in filter
        .field >= val   — greater-or-equal in filter
        .field <= val   — less-or-equal in filter
    """
    query = query.strip()

    # Handle pipe operations
    if "|" in query:
        parts = query.split("|", 1)
        left = _jq_query(data, parts[0].strip())
        return _jq_pipe(left, parts[1].strip())

    # Handle filter expressions [?...]
    filter_match = re.match(r"^(.+?)\[\?(.+?)\](.*)$", query)
    if filter_match:
        base_query = filter_match.group(1).strip()
        filter_expr = filter_match.group(2).strip()
        tail = filter_match.group(3).strip()
        items = _jq_query(data, base_query) if base_query else data
        if not isinstance(items, list):
            items = [items]
        result = [item for item in items if _eval_filter(item, filter_expr)]
        # Apply trailing access to EACH filtered item
        if tail:
            out: list[Any] = []
            for item in result:
                r = _jq_query(item, tail.lstrip(".") if tail.startswith(".") else tail)
                if isinstance(r, list):
                    out.extend(r)
                else:
                    out.append(r)
            return out
        return result

    # Handle array iteration: .field[] or []
    iter_match = re.match(r"^(.*?)\[\](.*)$", query)
    if iter_match:
        base = iter_match.group(1).strip()
        tail = iter_match.group(2).strip()
        if base:
            items = _resolve_path(data, base)
        else:
            items = data
        if not isinstance(items, list):
            items = [items]
        # Apply trailing access to EACH item individually
        if tail:
            results: list[Any] = []
            for item in items:
                # Always use _jq_query for the tail (handles .name, .employees[], etc.)
                r = _jq_query(item, tail.lstrip(".") if tail.startswith(".") else tail)
                if isinstance(r, list):
                    results.extend(r)
                else:
                    results.append(r)
            return results
        return items

    # Handle array index: [N]
    index_match = re.match(r"^\[(\d+)\]$", query)
    if index_match:
        idx = int(index_match.group(1))
        if isinstance(data, list) and idx < len(data):
            return data[idx]
        raise IndexError(f"index {idx} out of range")

    # Simple dot-notation path
    return _resolve_path(data, query)


def _resolve_path(data: Any, path: str) -> Any:
    """Resolve a dot-notation path like 'config.database.host' or 'items[0]'."""
    if not path or path == ".":
        return data
    parts = path.strip(".").split(".")
    current = data
    for part in parts:
        # Handle array index: field[0] or [0]
        idx_match = re.match(r"^(.+?)\[(\d+)\]$", part)
        if idx_match:
            field = idx_match.group(1)
            idx = int(idx_match.group(2))
            if field:
                current = current[field] if isinstance(current, dict) and field in current else None
            if isinstance(current, list) and idx < len(current):
                current = current[idx]
            else:
                raise KeyError(f"index {idx} out of range for '{part}'")
        elif isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise KeyError(f"field '{part}' not found")
    return current


def _jq_pipe(value: Any, op: str) -> Any:
    """Apply a pipe operation to a value."""
    op = op.strip()

    # Nested pipe MUST be checked first — before any other handler sees `|`.
    if "|" in op:
        first, rest = op.split("|", 1)
        intermediate = _jq_pipe(value, first.strip())
        return _jq_pipe(intermediate, rest.strip())

    if op == "length":
        if isinstance(value, (list, dict, str)):
            return len(value)
        raise ValueError(f"cannot get length of {type(value).__name__}")
    if op == "keys":
        # jq's `keys` returns keys SORTED (use keys_unsorted for insertion order).
        if isinstance(value, dict):
            return sorted(value.keys())
        raise ValueError(f"cannot get keys of {type(value).__name__}")
    if op == "keys_unsorted":
        if isinstance(value, dict):
            return list(value.keys())
        raise ValueError(f"cannot get keys of {type(value).__name__}")
    if op == "values":
        if isinstance(value, dict):
            return list(value.values())
        raise ValueError(f"cannot get values of {type(value).__name__}")
    if op == "type":
        return type(value).__name__
    if op == "flatten":
        if isinstance(value, list):
            flat: list[Any] = []
            for sub in value:
                if isinstance(sub, list):
                    flat.extend(sub)
                else:
                    flat.append(sub)
            return flat
        raise ValueError("flatten requires a list")
    # Handle select(expr) — filter a list by a condition
    select_match = re.match(r"^select\((.+)\)$", op)
    if select_match:
        if not isinstance(value, list):
            return value
        expr = select_match.group(1).strip()
        return [item for item in value if _eval_filter(item, expr)]
    # Handle map(select(expr)) — filter then return full items
    map_select_match = re.match(r"^map\(select\((.+)\)\)$", op)
    if map_select_match:
        if not isinstance(value, list):
            return value
        expr = map_select_match.group(1).strip()
        return [item for item in value if _eval_filter(item, expr)]
    # Handle field access: .name, .employees, .items[].sku, etc.
    if op.startswith("."):
        # Use _jq_query for field access so it handles [], filters, and nested patterns.
        # _resolve_path only handles simple dot-notation (no []).
        if isinstance(value, dict):
            return _jq_query(value, op)
        if isinstance(value, list):
            results: list[Any] = []
            for item in value:
                r = _jq_query(item, op) if isinstance(item, dict) else item
                if isinstance(r, list):
                    results.extend(r)
                else:
                    results.append(r)
            return results
        raise ValueError(f"cannot access field on {type(value).__name__}")
    # Handle string operations: contains([...])
    contains_match = re.match(r"^contains\(\[(.+)\]\)$", op)
    if contains_match:
        if isinstance(value, list):
            target = contains_match.group(1).strip().strip('"').strip("'")
            return target in value
        return False
    # Handle contains("string") — does the list contain this string?
    contains_str_match = re.match(r'^contains\("(.+)"\)$', op)
    if contains_str_match:
        if isinstance(value, list):
            return contains_str_match.group(1) in value
        return False
    raise ValueError(f"unknown pipe operation: '{op}'")


# Sentinel for a filter field an item doesn't have. jq treats a missing field as
# null and simply skips the item rather than erroring the whole query.
_MISSING = object()


def _resolve_filter_field(item: Any, field_path: str) -> Any:
    """Resolve a filter's field path, returning _MISSING instead of raising when absent."""
    if not isinstance(item, dict):
        return item
    try:
        return _resolve_path(item, field_path)
    except (KeyError, IndexError, TypeError):
        return _MISSING


def _eval_filter(item: Any, expr: str) -> bool:
    """Evaluate a simple filter like 'age > 30' or 'active == true'.

    A missing field is jq's `null`: the item is skipped (it matches only '!='),
    and a comparison that would raise on mismatched types (e.g. str vs int) skips
    the item too — so one odd row never fails the whole query.
    """
    match = re.match(r"^(\S+)\s*(==|!=|>=|<=|>|<)\s*(.+)$", expr.strip())
    if not match:
        # Simple truthiness, e.g. [?active]
        resolved = _resolve_filter_field(item, expr.lstrip("."))
        return resolved is not _MISSING and bool(resolved)

    field_path = match.group(1).lstrip(".")
    op = match.group(2)
    raw_val = match.group(3).strip()

    value = _resolve_filter_field(item, field_path)

    # Parse the comparison value
    comp_val: Any
    if raw_val in ("true", "false"):
        comp_val = raw_val == "true"
    elif raw_val.startswith('"') and raw_val.endswith('"'):
        comp_val = raw_val[1:-1]
    elif raw_val.startswith("'") and raw_val.endswith("'"):
        comp_val = raw_val[1:-1]
    else:
        try:
            comp_val = int(raw_val)
        except ValueError:
            try:
                comp_val = float(raw_val)
            except ValueError:
                comp_val = raw_val

    if value is _MISSING:
        # Missing field == jq null: only "!=" matches a concrete value.
        return op == "!="

    try:
        if op == "==":
            return bool(value == comp_val)
        if op == "!=":
            return bool(value != comp_val)
        if op == ">":
            return bool(value > comp_val)
        if op == "<":
            return bool(value < comp_val)
        if op == ">=":
            return bool(value >= comp_val)
        if op == "<=":
            return bool(value <= comp_val)
    except TypeError:
        # Incomparable types → item doesn't match; skip rather than crash the query.
        return False
    return False
