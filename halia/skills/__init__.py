"""skills: horizontal skill library (fs, ingest, research, tables…)."""

from __future__ import annotations

from collections.abc import Callable

from halia.skills.base import Skill
from halia.skills.calc import Calculate
from halia.skills.chart import MakeChart
from halia.skills.data import AggregateCsv, ReadCsv
from halia.skills.db import QueryDb
from halia.skills.excel import ReadExcel
from halia.skills.exec import RunCommand
from halia.skills.export import MakePdf, MakePptx
from halia.skills.fs import ListFiles, ReadFile, WriteFile
from halia.skills.pdf import ReadPdf
from halia.skills.readability import Readability
from halia.skills.reconcile import ReconcileCsv
from halia.skills.registry import SkillRegistry
from halia.skills.web import FetchUrl, WebSearch

# The full catalogue of skills, by name.
_SKILL_FACTORIES: dict[str, Callable[[], Skill]] = {
    "read_file": ReadFile,
    "write_file": WriteFile,
    "list_files": ListFiles,
    "fetch_url": FetchUrl,
    "web_search": WebSearch,
    "calculate": Calculate,
    "read_csv": ReadCsv,
    "aggregate_csv": AggregateCsv,
    "read_excel": ReadExcel,
    "read_pdf": ReadPdf,
    "reconcile_csv": ReconcileCsv,
    "query_db": QueryDb,
    "readability": Readability,
    "make_chart": MakeChart,
    "make_pdf": MakePdf,
    "make_pptx": MakePptx,
    "run_command": RunCommand,
}

# Always included, regardless of profile: deterministic compute is part of the
# trust floor — without it the model would do arithmetic in its head.
_ALWAYS = ["calculate"]

# The default selection (everything safe/useful; run_command is opt-in).
DEFAULT_SKILLS = [
    "read_file",
    "write_file",
    "list_files",
    "fetch_url",
    "web_search",
    "calculate",
    "read_csv",
    "aggregate_csv",
    "read_excel",
    "read_pdf",
    "reconcile_csv",
    "query_db",
]


def available_skills() -> list[str]:
    """All known skill names."""
    return list(_SKILL_FACTORIES)


def build_registry(skill_names: list[str]) -> SkillRegistry:
    """Build a registry from a list of skill names (`_ALWAYS` prepended, deduped)."""
    registry = SkillRegistry()
    for name in dict.fromkeys(_ALWAYS + skill_names):
        factory = _SKILL_FACTORIES.get(name)
        if factory is not None:
            registry.register(factory())
    return registry


def default_registry(allow_commands: bool = False) -> SkillRegistry:
    """The default registry — safe skills always, run_command only if allowed."""
    names = list(DEFAULT_SKILLS)
    if allow_commands:
        names.append("run_command")
    return build_registry(names)
