"""save_procedure — let halia persist a taught test procedure from a conversation.

This is the model-callable side of teaching. In chat, the user can describe a test in
their own words ("first POST here, then check the status, output a table"); halia reasons
it into slots and calls this to REMEMBER it — the same store the `halia procedure` CLI and
`/procedure teach` wizard write to. Either the user drives the teach, or halia does; both
converge on one store.

Gated (`dangerous=True`): saving is a persistent change, so it goes through the approval
gate — that gate IS the confirm step (the user sees exactly what will be saved and can say
no). Merge semantics: fields the caller omits are LEFT UNCHANGED, so halia can build a
procedure up over several turns ("add a pass rule") without clobbering the rest.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")


class SaveProcedure:
    name = "save_procedure"
    description = (
        "Save (create or update) a reusable test procedure so halia REMEMBERS it for later "
        "runs. Use this when the user describes a test/task they'll want to repeat. Fields you "
        "omit are left unchanged, so you can build a procedure up over several turns. A "
        "procedure needs: target (what's tested), data_spec (+ data_source), an action (a url "
        "OR ordered steps), result_columns (output), and pass_rule (deterministic verdict). "
        "State plainly what you'll save and confirm with the user before calling this."
    )
    dangerous = True
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "name": {"type": "string", "description": "Procedure name (how it'll be run)."},
            "target": {"type": "string", "description": "What is under test."},
            "data_spec": {"type": "string", "description": "The test data's shape/rows."},
            "data_source": {
                "type": "string",
                "enum": ["synthesize", "provided"],
                "description": "'synthesize' (halia generates data) or 'provided' (user supplies).",
            },
            "steps": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Ordered steps for a multi-step 'first do X, then Y' procedure.",
            },
            "method": {"type": "string", "enum": list(_METHODS), "description": "HTTP method."},
            "url": {"type": "string", "description": "Endpoint URL (if it calls one)."},
            "headers": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "Default request headers.",
            },
            "result_columns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Exact output CSV columns.",
            },
            "pass_rule": {"type": "string", "description": "Deterministic pass/fail rule."},
            "description": {"type": "string", "description": "Short description."},
        },
        "required": ["name"],
    }

    def run(self, args: dict[str, Any]) -> str:
        from halia.procedures import Procedure, get_procedure, save_procedure

        name = args.get("name")
        if not isinstance(name, str) or not name.strip():
            return "error: 'name' is required and must be a non-empty string"
        name = name.strip()

        base = get_procedure(name) or Procedure(name=name)
        updates: dict[str, Any] = {}
        for key in ("description", "target", "data_spec", "pass_rule", "url"):
            if isinstance(args.get(key), str):
                updates[key] = args[key]
        if isinstance(args.get("method"), str) and args["method"].upper() in _METHODS:
            updates["method"] = args["method"].upper()
        if args.get("data_source") in ("synthesize", "provided"):
            updates["data_source"] = args["data_source"]
        if isinstance(args.get("steps"), list):
            updates["steps"] = [str(s) for s in args["steps"]]
        if isinstance(args.get("result_columns"), list):
            updates["result_columns"] = [str(c) for c in args["result_columns"]]
        if isinstance(args.get("headers"), dict):
            updates["headers"] = {str(k): str(v) for k, v in args["headers"].items()}

        proc = replace(base, **updates)
        save_procedure(proc)
        missing = proc.missing_slots()
        status = (
            "ready to run"
            if not missing
            else f"still missing before it can run: {', '.join(missing)}"
        )
        return (
            f"saved procedure '{name}' ({status}). "
            f"Run it with `halia procedure run {name}` or `/procedure run {name}`."
        )
