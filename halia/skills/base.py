"""Skill abstraction.

A skill is one capability the agent can call: a name, a description, a JSON-Schema
for its parameters, and a `run`. Skills are exposed to the model as OpenAI-style
tool schemas and executed by the agent loop.
"""

from __future__ import annotations

from typing import Any, Protocol


class Skill(Protocol):
    """One callable capability."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    dangerous: bool  # consequential (write/delete/exec) → requires approval to run
    untrusted: bool  # ingests external data (web, user files) → quarantine observation

    def run(self, args: dict[str, Any]) -> str:
        """Execute with parsed arguments; return a text observation."""
        ...


def to_tool_schema(skill: Skill) -> dict[str, Any]:
    """Render a skill as an OpenAI-style tool/function schema."""
    return {
        "type": "function",
        "function": {
            "name": skill.name,
            "description": skill.description,
            "parameters": skill.parameters,
        },
    }
