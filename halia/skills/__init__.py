"""skills: horizontal skill library (fs, ingest, research, tables…)."""

from __future__ import annotations

from collections.abc import Callable

from halia.skills.base import Skill
from halia.skills.calc import Calculate
from halia.skills.chart import MakeChart
from halia.skills.clean import CleanCsv
from halia.skills.compliance import CheckRequirements
from halia.skills.data import AggregateCsv, GroupByCsv, ReadCsv
from halia.skills.db import QueryData, QueryDb
from halia.skills.excel import ReadExcel
from halia.skills.exec import RunCommand
from halia.skills.expectation import CheckExpectation
from halia.skills.export import MakeDocx, MakePdf, MakePptx
from halia.skills.fs import ListFiles, ReadFile, WriteFile
from halia.skills.http import HttpRequest
from halia.skills.pdf import ReadPdf
from halia.skills.qa import CheckQaArtifact
from halia.skills.readability import Readability
from halia.skills.reconcile import ReconcileCsv
from halia.skills.registry import SkillRegistry
from halia.skills.spreadsheet import MakeExcel
from halia.skills.textmetrics import CountText
from halia.skills.web import FetchUrl, WebSearch
from halia.skills.word import ReadDocx

# The full catalogue of skills, by name.
_SKILL_FACTORIES: dict[str, Callable[[], Skill]] = {
    "read_file": ReadFile,
    "write_file": WriteFile,
    "list_files": ListFiles,
    "fetch_url": FetchUrl,
    "web_search": WebSearch,
    "http_request": HttpRequest,
    "calculate": Calculate,
    "read_csv": ReadCsv,
    "aggregate_csv": AggregateCsv,
    "group_by": GroupByCsv,
    "clean_csv": CleanCsv,
    "read_excel": ReadExcel,
    "read_pdf": ReadPdf,
    "read_docx": ReadDocx,
    "check_requirements": CheckRequirements,
    "check_qa_artifact": CheckQaArtifact,
    "check_expectation": CheckExpectation,
    "reconcile_csv": ReconcileCsv,
    "query_db": QueryDb,
    "query_data": QueryData,
    "readability": Readability,
    "count_text": CountText,
    "make_chart": MakeChart,
    "make_pdf": MakePdf,
    "make_pptx": MakePptx,
    "make_docx": MakeDocx,
    "make_excel": MakeExcel,
    "run_command": RunCommand,
}

# Always included, regardless of profile: deterministic compute is part of the
# trust floor — without it the model would do arithmetic in its head.
_ALWAYS = ["calculate"]

# The default (no-profile) agent is the full generalist: EVERY safe skill. Only
# run_command is held back (opt-in via --allow-commands). Deriving this from the
# catalogue means any new skill auto-joins the default — it can't silently drift
# behind the verticals again.
DEFAULT_SKILLS = [name for name in _SKILL_FACTORIES if name != "run_command"]


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
