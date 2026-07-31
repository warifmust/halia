"""skills: horizontal skill library (fs, ingest, research, tables…)."""

from __future__ import annotations

from halia.skills.fs import ListFiles, ReadFile
from halia.skills.registry import SkillRegistry


def default_registry(allow_commands: bool = False) -> SkillRegistry:
    """Build the skill registry.

    Safe read-only skills are always included. `run_command` is *opt-in*
    (`allow_commands=True`) because it is dangerous — and even then, the loop
    still requires per-call approval before it runs.
    """
    registry = SkillRegistry()
    registry.register(ReadFile())
    registry.register(ListFiles())
    if allow_commands:
        from halia.skills.exec import RunCommand

        registry.register(RunCommand())
    return registry
