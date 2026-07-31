"""Skill registry — the set of capabilities available to a run."""

from __future__ import annotations

from typing import Any

from halia.skills.base import Skill, to_tool_schema


class SkillRegistry:
    """Holds the skills available to the agent and renders their tool schemas."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def all(self) -> list[Skill]:
        return list(self._skills.values())

    def tool_schemas(self) -> list[dict[str, Any]]:
        return [to_tool_schema(s) for s in self._skills.values()]
