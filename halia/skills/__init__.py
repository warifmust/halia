"""skills: horizontal skill library (fs, ingest, research, tables…)."""

from __future__ import annotations

from halia.computer.cua_backend import cua_available
from halia.skills.ask import AskUser
from halia.skills.base import Skill as Skill  # noqa: F401 — re-exported for external use
from halia.skills.calc import Calculate
from halia.skills.chart import MakeChart
from halia.skills.clean import CleanCsv
from halia.skills.compliance import CheckRequirements
from halia.skills.data import AggregateCsv, GroupByCsv, ReadCsv
from halia.skills.db import QueryData, QueryDb
from halia.skills.diagram import MakeDiagram, MakeErDiagram
from halia.skills.excel import ReadExcel
from halia.skills.exec import RunCommand
from halia.skills.expectation import CheckExpectation
from halia.skills.export import MakeDocx, MakePdf, MakePptx
from halia.skills.fs import ListFiles, ReadFile, WriteFile
from halia.skills.grep import GrepFile
from halia.skills.http import HttpRequest
from halia.skills.jq import JqQuery
from halia.skills.openapi_tool import OpenApiLookup
from halia.skills.pdf import ReadPdf
from halia.skills.procedure_tool import SaveProcedure
from halia.skills.qa import CheckQaArtifact
from halia.skills.readability import Readability
from halia.skills.reconcile import ReconcileCsv
from halia.skills.reference import LearnFromReference, SaveReference, TeachHistory
from halia.skills.registry import SkillRegistry
from halia.skills.search import SearchCode
from halia.skills.spreadsheet import MakeExcel
from halia.skills.textmetrics import CountText
from halia.skills.web import FetchUrl, WebSearch
from halia.skills.word import ReadDocx

# Browser automation (optional — requires playwright)
try:
    from halia.skills.browser import (
        BrowserClick,
        BrowserClose,
        BrowserEnsure,
        BrowserExtract,
        BrowserNavigate,
        BrowserNewTab,
        BrowserOpen,
        BrowserRead,
        BrowserScreenshot,
        BrowserScroll,
        BrowserSwitchTab,
        BrowserType,
        BrowserWait,
    )
    _HAS_BROWSER = True
except ImportError:
    _HAS_BROWSER = False

# CUA (Computer Use Agent) — optional, requires cua-driver
try:
    from halia.skills.cua import (
        CuaClick,
        CuaDesktopState,
        CuaDoubleClick,
        CuaHotkey,
        CuaOpenUrl,
        CuaPressKey,
        CuaScreenshot,
        CuaScroll,
        CuaType,
    )
    _HAS_CUA = True
except ImportError:
    _HAS_CUA = False

# The full catalogue of skills, by name.
_SKILL_FACTORIES: dict[str, type] = {
    "read_file": ReadFile,
    "write_file": WriteFile,
    "list_files": ListFiles,
    "grep_file": GrepFile,
    "jq_query": JqQuery,
    "fetch_url": FetchUrl,
    "web_search": WebSearch,
    "http_request": HttpRequest,
    "openapi_lookup": OpenApiLookup,
    "calculate": Calculate,
    "ask_user": AskUser,
    "read_csv": ReadCsv,
    "aggregate_csv": AggregateCsv,
    "group_by": GroupByCsv,
    "clean_csv": CleanCsv,
    "read_excel": ReadExcel,
    "read_pdf": ReadPdf,
    "read_docx": ReadDocx,
    "search_code": SearchCode,
    "check_requirements": CheckRequirements,
    "check_qa_artifact": CheckQaArtifact,
    "check_expectation": CheckExpectation,
    "save_procedure": SaveProcedure,
    "reconcile_csv": ReconcileCsv,
    "query_db": QueryDb,
    "query_data": QueryData,
    "readability": Readability,
    "count_text": CountText,
    "make_chart": MakeChart,
    "make_diagram": MakeDiagram,
    "make_er_diagram": MakeErDiagram,
    "make_pdf": MakePdf,
    "make_pptx": MakePptx,
    "make_docx": MakeDocx,
    "make_excel": MakeExcel,
    "run_command": RunCommand,
    "learn_from_reference": LearnFromReference,
    "save_reference": SaveReference,
    "teach_history": TeachHistory,
}

# Computer backends are BLENDED by default: browser (Playwright) and CUA (desktop)
# are both registered when available, and the model picks per task. `computer_backend`
# can force one backend: "auto" (default), "browser", or "cua".
def _get_computer_backend() -> str:
    """Get the configured computer backend: 'auto', 'browser', or 'cua'."""
    try:
        from halia.config.settings import read_config
        backend = str(read_config().get("computer_backend", "auto"))
        # Legacy: "halia" was the old name for the built-in browser backend.
        if backend == "halia":
            backend = "browser"
        # Unknown values must not silently disable computer automation.
        if backend not in ("auto", "browser", "cua"):
            backend = "auto"
        return backend
    except Exception:
        return "auto"

_backend = _get_computer_backend()

# CUA drives a real desktop; on headless systems cua-driver cannot run. Treat
# it as unavailable and keep browser skills as the fallback instead of hiding
# them behind a broken CUA backend.
_cua_usable = _HAS_CUA and cua_available()

# Browser skills: blended, browser-forced, or cua-forced-but-unrunnable (fallback).
_browser_on = _HAS_BROWSER and (
    _backend in ("auto", "browser") or (_backend == "cua" and not _cua_usable)
)
# CUA skills: blended or cua-forced, and only when a display actually exists.
_cua_on = _cua_usable and _backend in ("auto", "cua")

if _browser_on:
    _SKILL_FACTORIES.update({
        "browser_open": BrowserOpen,
        "browser_navigate": BrowserNavigate,
        "browser_new_tab": BrowserNewTab,
        "browser_click": BrowserClick,
        "browser_type": BrowserType,
        "browser_screenshot": BrowserScreenshot,
        "browser_read": BrowserRead,
        "browser_extract": BrowserExtract,
        "browser_scroll": BrowserScroll,
        "browser_switch_tab": BrowserSwitchTab,
        "browser_wait": BrowserWait,
        "browser_ensure": BrowserEnsure,
        "browser_close": BrowserClose,
    })

# CUA skills — blended or forced, and only when they can actually run.
if _cua_on:
    _SKILL_FACTORIES.update({
        "cua_screenshot": CuaScreenshot,
        "cua_click": CuaClick,
        "cua_double_click": CuaDoubleClick,
        "cua_type": CuaType,
        "cua_scroll": CuaScroll,
        "cua_desktop": CuaDesktopState,
        "cua_open_url": CuaOpenUrl,
        "cua_press_key": CuaPressKey,
        "cua_hotkey": CuaHotkey,
    })

# Always included, regardless of profile: deterministic compute is part of the
# trust floor — without it the model would do arithmetic in its head.
_ALWAYS = ["calculate"]

# The default (no-profile) agent is the full generalist: EVERY safe skill. Only
# run_command is held back (opt-in via --allow-commands). Deriving this from the
# catalogue means any new skill auto-joins the default — it can't silently drift
# behind the verticals again.
DEFAULT_SKILLS = [name for name in _SKILL_FACTORIES if name != "run_command"]


def available_backends() -> set[str]:
    """Computer backends currently registered: subset of {'browser', 'cua'}."""
    backends: set[str] = set()
    if any(name.startswith("browser_") for name in _SKILL_FACTORIES):
        backends.add("browser")
    if any(name.startswith("cua_") for name in _SKILL_FACTORIES):
        backends.add("cua")
    return backends


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
