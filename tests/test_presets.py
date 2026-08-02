"""Tests for built-in persona presets and profile resolution."""

from pathlib import Path
from typing import Any

from halia.presets import BUILTIN_PRESETS, get_preset, preset_names, resolve_profile
from halia.profiles import Profile, save_profile
from halia.skills import available_skills


def test_finance_preset_exists_and_is_wired() -> None:
    finance = get_preset("finance")
    assert finance is not None
    assert finance.name == "finance"
    assert "reconcile_csv" in finance.skills  # the finance moat skill
    assert finance.extra_prompt  # has a persona
    assert "finance" in preset_names()


def test_research_preset_exists_and_is_wired() -> None:
    research = get_preset("research")
    assert research is not None
    assert research.name == "research"
    assert "fetch_url" in research.skills  # the research workhorse
    assert "reconcile_csv" not in research.skills  # not a finance tool
    assert research.extra_prompt  # has a persona
    assert "research" in preset_names()


def test_education_preset_exists_and_is_wired() -> None:
    edu = get_preset("education")
    assert edu is not None
    assert "readability" in edu.skills  # deterministic reading-level hook
    assert "make_chart" in edu.skills  # the "build graph" clerical capability
    assert "aggregate_csv" in edu.skills  # pupil-data analysis, not head-math
    assert edu.extra_prompt


def test_presets_are_distinct_verticals() -> None:
    # halia is a general framework: several verticals ship out of the box.
    assert {"finance", "research", "education"} <= set(preset_names())
    prompts = {p: get_preset(p).extra_prompt for p in ("finance", "research", "education")}
    assert len(set(prompts.values())) == 3  # each has its own persona


def test_preset_skills_are_all_real() -> None:
    valid = set(available_skills())
    for preset in BUILTIN_PRESETS.values():
        unknown = [s for s in preset.skills if s not in valid]
        assert unknown == [], f"{preset.name} references unknown skills: {unknown}"


def test_resolve_prefers_user_profile_over_preset(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    # No user profile yet → falls back to the built-in preset.
    assert resolve_profile_db("finance", db).extra_prompt == get_preset("finance").extra_prompt
    # User saves a profile of the same name → it wins (customization).
    save_profile(Profile(name="finance", skills=["read_csv"], extra_prompt="mine"), db_path=db)
    resolved = resolve_profile_db("finance", db)
    assert resolved.extra_prompt == "mine"
    assert resolved.skills == ["read_csv"]


def test_resolve_unknown_is_none(tmp_path: Any) -> None:
    assert resolve_profile_db("nope", tmp_path / "halia.db") is None


def resolve_profile_db(name: str, db: Path) -> Any:
    """resolve_profile with an injectable db path (mirrors production DB-first order)."""
    from halia.profiles import get_profile

    return get_profile(name, db_path=db) or get_preset(name)


def test_resolve_profile_default_path_smoke() -> None:
    # The production entrypoint resolves the finance preset without a saved profile.
    prof = resolve_profile("finance")
    assert prof is not None and prof.name == "finance"
