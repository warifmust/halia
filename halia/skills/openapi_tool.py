"""openapi_lookup — resolve an OpenAPI/Swagger spec and look up its real contract.

The problem this solves: given a docs URL or an operationId, the model used to guess the
endpoint path (and the request shape), burning iterations and inventing payloads. This tool
resolves the spec (a docs URL via `resolve_openapi_spec`, or a local JSON file), then answers:
- no filter  → an INDEX of operations (METHOD path — operationId — summary)
- operation_id → that operation's method, full path, parameters, request-body schema + example
- path/query  → the operations matching a path or keyword

Read-only: URL fetches are egress-floored; file reads go through the permission floor. The
spec is external data, so results are quarantined as untrusted.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from halia.permissions.guard import PermissionDenied, check_readable

_METHODS = ("get", "post", "put", "patch", "delete", "head", "options", "trace")
_MAX_INDEX = 150  # cap the operation index so a huge spec doesn't flood context
_MAX_EXAMPLE_DEPTH = 5


def _load_spec(spec: str) -> tuple[dict[str, Any] | None, str]:
    """Return (spec_dict, source) or (None, error_message)."""
    spec = spec.strip()
    if spec.startswith(("http://", "https://")):
        from halia.openapi import resolve_openapi_spec
        from halia.skills.web import fetch_url_raw

        spec_url = resolve_openapi_spec(spec)
        if not spec_url:
            return None, (
                f"error: could not find an OpenAPI/Swagger spec at {spec}. If it's a "
                "localhost/dev spec, make sure local egress is on (--allow-local or /local on)."
            )
        try:
            body = fetch_url_raw(spec_url)[1]
        except Exception as exc:  # noqa: BLE001 — surface the fetch failure as a tool error
            return None, f"error: fetching spec {spec_url} failed: {exc}"
        try:
            return json.loads(body), spec_url
        except json.JSONDecodeError as exc:
            return None, f"error: spec at {spec_url} is not valid JSON ({exc})"

    path = Path(spec).expanduser()
    try:
        check_readable(path)
    except PermissionDenied as exc:
        return None, f"blocked: {exc}"
    if not path.is_file():
        return None, f"error: not a file: {path}"
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace")), str(path)
    except json.JSONDecodeError as exc:
        return None, f"error: {path} is not valid JSON ({exc})"


def _deref(spec: dict[str, Any], node: Any) -> dict[str, Any]:
    """Follow a single `$ref` (one hop) into components; return {} on miss."""
    if isinstance(node, dict) and "$ref" in node:
        ref = node["$ref"]
        if isinstance(ref, str) and ref.startswith("#/"):
            cur: Any = spec
            for part in ref[2:].split("/"):
                if not isinstance(cur, dict) or part not in cur:
                    return {}
                cur = cur[part]
            return cur if isinstance(cur, dict) else {}
        return {}
    return node if isinstance(node, dict) else {}


def _iter_ops(spec: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    ops: list[tuple[str, str, dict[str, Any]]] = []
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        return ops
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        for method in _METHODS:
            op = item.get(method)
            if isinstance(op, dict):
                ops.append((method.upper(), str(path), op))
    return ops


def _servers(spec: dict[str, Any]) -> str:
    servers = spec.get("servers")
    servers = servers if isinstance(servers, list) else []
    urls = [s.get("url", "") for s in servers if isinstance(s, dict) and s.get("url")]
    return ", ".join(urls) if urls else "(none declared — paths are relative to the API base)"


def _example(spec: dict[str, Any], schema: Any, depth: int = 0) -> Any:
    schema = _deref(spec, schema)
    if not schema or depth > _MAX_EXAMPLE_DEPTH:
        return None
    if "example" in schema:
        return schema["example"]
    if "default" in schema:
        return schema["default"]
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]
    t = schema.get("type")
    if t == "object" or "properties" in schema:
        props = schema.get("properties", {})
        return {
            name: _example(spec, sub, depth + 1)
            for name, sub in props.items()
            if isinstance(props, dict)
        }
    if t == "array":
        item = _example(spec, schema.get("items", {}), depth + 1)
        return [item] if item is not None else []
    if t in ("integer", "number"):
        return 0
    if t == "boolean":
        return False
    if t == "string":
        return {
            "date-time": "2024-01-01T00:00:00Z",
            "date": "2024-01-01",
            "email": "user@example.com",
            "uuid": "00000000-0000-0000-0000-000000000000",
        }.get(str(schema.get("format", "")), "string")
    return None


def _params(spec: dict[str, Any], op: dict[str, Any], path_item: dict[str, Any]) -> list[str]:
    raw = []
    for src in (path_item.get("parameters"), op.get("parameters")):
        if isinstance(src, list):
            raw.extend(src)
    lines: list[str] = []
    for p in raw:
        p = _deref(spec, p)
        if not p:
            continue
        name = p.get("name", "?")
        loc = p.get("in", "?")
        required = "required" if p.get("required") else "optional"
        typ = _deref(spec, p.get("schema", {})).get("type", "?")
        lines.append(f"  - {name} ({loc}, {required}, {typ})")
    return lines


def _body(spec: dict[str, Any], op: dict[str, Any]) -> list[str]:
    rb = _deref(spec, op.get("requestBody", {}))
    if not rb:
        return []
    content = rb.get("content", {})
    if not isinstance(content, dict) or not content:
        return []
    media = content.get("application/json") or next(iter(content.values()))
    schema = _deref(spec, media.get("schema", {})) if isinstance(media, dict) else {}
    required = "required" if rb.get("required") else "optional"
    lines = [f"request body (application/json, {required}):"]
    props = schema.get("properties", {})
    if isinstance(props, dict) and props:
        req = set(schema.get("required", []) if isinstance(schema.get("required"), list) else [])
        for name, sub in props.items():
            sub = _deref(spec, sub)
            enum = sub.get("enum")
            extra = f" enum={enum}" if isinstance(enum, list) else ""
            mark = "required" if name in req else "optional"
            lines.append(f"  - {name} ({sub.get('type', '?')}, {mark}){extra}")
    example = _example(spec, schema)
    if example is not None:
        lines.append("example:")
        lines.append(json.dumps(example, indent=2)[:2000])
    return lines


def _render_op(spec: dict[str, Any], source: str, method: str, path: str, op: dict[str, Any],
               path_item: dict[str, Any]) -> str:
    out = [f"source: {source}", f"servers: {_servers(spec)}", "", f"{method} {path}"]
    oid = op.get("operationId")
    if oid:
        out.append(f"operationId: {oid}")
    summ = op.get("summary") or op.get("description")
    if summ:
        out.append(f"summary: {str(summ)[:200]}")
    params = _params(spec, op, path_item)
    if params:
        out.append("parameters:")
        out.extend(params)
    out.extend(_body(spec, op))
    return "\n".join(out)


class OpenApiLookup:
    name = "openapi_lookup"
    description = (
        "Resolve an OpenAPI/Swagger spec (a docs URL or a local JSON file) and look up its REAL "
        "contract — use this before http_request against a documented API, so you never guess a "
        "path or invent a payload. No filter → an index of operations (METHOD path — "
        "operationId — summary). Pass `operation_id` (or `path`) to get that operation's method, "
        "full path, parameters, request-body schema, and a minimal example payload. `query` "
        "filters the index by keyword."
    )
    dangerous = False  # read-only; URL fetch is egress-floored, file read is floor-guarded
    untrusted = True  # spec content is external data
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "spec": {
                "type": "string",
                "description": "OpenAPI/Swagger docs URL or a local spec JSON file path.",
            },
            "operation_id": {
                "type": "string",
                "description": "Return full detail for this operationId.",
            },
            "path": {
                "type": "string",
                "description": "Return operations whose path contains this substring.",
            },
            "query": {
                "type": "string",
                "description": "Filter the operation index by keyword (path/operationId/summary).",
            },
        },
        "required": ["spec"],
    }

    def run(self, args: dict[str, Any]) -> str:
        spec_arg = args.get("spec")
        if not isinstance(spec_arg, str) or not spec_arg.strip():
            return "error: 'spec' is required and must be a non-empty string"
        spec, source = _load_spec(spec_arg)
        if spec is None:
            return source  # already an error/blocked message
        ops = _iter_ops(spec)
        if not ops:
            return f"error: no operations found in the spec at {source}"

        operation_id = args.get("operation_id")
        path_filter = args.get("path")
        query = args.get("query")

        # Specific operation by id.
        if isinstance(operation_id, str) and operation_id.strip():
            want = operation_id.strip().casefold()
            paths = spec.get("paths", {})
            for method, path, op in ops:
                if str(op.get("operationId", "")).casefold() == want:
                    item = paths.get(path, {}) if isinstance(paths, dict) else {}
                    return _render_op(spec, source, method, path, op, item)
            near = [
                op.get("operationId")
                for _, _, op in ops
                if want in str(op.get("operationId", "")).casefold()
            ]
            hint = f" Did you mean: {', '.join(str(n) for n in near[:5])}?" if near else ""
            return f"error: no operation with operationId '{operation_id}' in {source}.{hint}"

        # Path filter → detail for each matching operation (capped).
        if isinstance(path_filter, str) and path_filter.strip():
            needle = path_filter.strip().casefold()
            paths = spec.get("paths", {})
            matches = [(m, p, o) for (m, p, o) in ops if needle in p.casefold()]
            if not matches:
                return f"error: no path containing '{path_filter}' in {source}"
            items = paths if isinstance(paths, dict) else {}
            blocks = [
                _render_op(spec, source, m, p, o, items.get(p, {}))
                for (m, p, o) in matches[:10]
            ]
            return "\n\n---\n\n".join(blocks)

        # Otherwise: the operation index (optionally keyword-filtered).
        lines = []
        for method, path, op in ops:
            oid = op.get("operationId", "")
            summ = (op.get("summary") or op.get("description") or "")
            lines.append(f"{method} {path}  ({oid}) — {str(summ)[:80]}".rstrip(" —"))
        if isinstance(query, str) and query.strip():
            q = query.strip().casefold()
            lines = [ln for ln in lines if q in ln.casefold()]
            if not lines:
                return f"error: no operation matching '{query}' in {source}"
        header = f"source: {source}\nservers: {_servers(spec)}\n{len(lines)} operation(s):"
        shown = lines[:_MAX_INDEX]
        extra = len(lines) - _MAX_INDEX
        footer = "" if extra <= 0 else f"\n… [{extra} more; filter with `query` or `path`]"
        return header + "\n" + "\n".join(shown) + footer
