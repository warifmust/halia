"""QA hook — check_qa_artifact (meta-QA).

Validate that a manual-QA artifact has its required fields. A bug report either has
concrete repro steps / expected / actual / environment or it doesn't; a test case
needs preconditions / steps / expected. Incomplete bug reports are the #1 manual-QA
friction — this catches them deterministically.

Presence is deterministic (is the field there, yes/no). Whether a present field is
ADEQUATE (the repro steps actually reproduce the bug) is a judgment the model must
flag as such — same presence-vs-adequacy split as check_requirements.
"""

from __future__ import annotations

from typing import Any

# Required fields per artifact, each with case-insensitive substring variants that
# count as "present". Kept reasonably strict — a false "present" is worse than a
# false "missing" for a completeness check.
_ARTIFACTS: dict[str, dict[str, list[str]]] = {
    "bug_report": {
        "steps to reproduce": ["steps to reproduce", "reproduce", "reproduction", "repro"],
        "expected result": ["expected"],
        "actual result": ["actual"],
        "environment": [
            "environment", "browser", "operating system", "platform",
            "device", "os version", "app version", "build",
        ],
    },
    "test_case": {
        "preconditions": ["precondition", "prerequisite", "pre-requisite", "setup"],
        "steps": ["step", "procedure", "action"],
        "expected result": ["expected"],
    },
}


class CheckQaArtifact:
    name = "check_qa_artifact"
    description = (
        "Validate that a QA artifact has its required fields. kind='bug_report' checks for "
        "steps-to-reproduce, expected result, actual result, and environment; "
        "kind='test_case' checks for preconditions, steps, and expected result. Reports each "
        "field PRESENT or MISSING. Presence is deterministic; whether a present field is "
        "adequate (do the steps actually reproduce?) is a separate judgment you must state."
    )
    dangerous = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "text": {"type": "string", "description": "The bug report or test case text."},
            "kind": {
                "type": "string",
                "enum": list(_ARTIFACTS),
                "description": "Which artifact to check.",
            },
        },
        "required": ["text", "kind"],
    }

    def run(self, args: dict[str, Any]) -> str:
        text = args.get("text")
        kind = args.get("kind")
        if not isinstance(text, str) or not text.strip():
            return "error: 'text' is required and must be non-empty"
        if kind not in _ARTIFACTS:
            return f"error: 'kind' must be one of: {', '.join(_ARTIFACTS)}"

        haystack = text.lower()
        fields = _ARTIFACTS[kind]
        present = [f for f, variants in fields.items() if any(v in haystack for v in variants)]
        missing = [f for f in fields if f not in present]

        lines = [f"QA completeness ({kind}): {len(present)}/{len(fields)} required fields present."]
        if present:
            lines.append("\nPRESENT (presence only — review adequacy):")
            lines.extend(f"- {f}" for f in present)
        if missing:
            lines.append(f"\nMISSING ({len(missing)}):")
            lines.extend(f"- {f}" for f in missing)
        else:
            lines.append("\nMISSING: none.")
        return "\n".join(lines)
