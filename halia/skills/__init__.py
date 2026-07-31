"""skills: horizontal skill library (fs, ingest, research, tables…)."""

from __future__ import annotations

from halia.skills.calc import Calculate
from halia.skills.data import AggregateCsv, ReadCsv
from halia.skills.db import QueryDb
from halia.skills.fs import ListFiles, ReadFile
from halia.skills.registry import SkillRegistry
from halia.skills.web import FetchUrl


def default_registry(allow_commands: bool = False) -> SkillRegistry:
    """Build the skill registry.

    Safe read-only skills are always included. `run_command` is *opt-in*
    (`allow_commands=True`) because it is dangerous — and even then, the loop
    still requires per-call approval before it runs.
    """
    registry = SkillRegistry()
    registry.register(ReadFile())
    registry.register(ListFiles())
    registry.register(FetchUrl())
    registry.register(Calculate())
    registry.register(ReadCsv())
    registry.register(AggregateCsv())
    registry.register(QueryDb())
    if allow_commands:
        from halia.skills.exec import RunCommand

        registry.register(RunCommand())
    return registry
