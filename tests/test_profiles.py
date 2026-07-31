"""Tests for profiles and profile-driven registry building."""

from typing import Any

from halia.profiles import (
    Profile,
    delete_profile,
    get_profile,
    list_profiles,
    save_profile,
)
from halia.skills import build_registry


def test_profile_roundtrip(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    save_profile(
        Profile(
            "finance",
            ["read_csv", "aggregate_csv"],
            model="deepseek-v4-pro",
            extra_prompt="You are a finance analyst.",
        ),
        db_path=db,
    )
    loaded = get_profile("finance", db_path=db)
    assert loaded is not None
    assert loaded.skills == ["read_csv", "aggregate_csv"]
    assert loaded.model == "deepseek-v4-pro"
    assert loaded.extra_prompt == "You are a finance analyst."


def test_list_and_delete(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    save_profile(Profile("a", []), db_path=db)
    save_profile(Profile("b", []), db_path=db)
    assert {p.name for p in list_profiles(db_path=db)} == {"a", "b"}
    assert delete_profile("a", db_path=db) is True
    assert delete_profile("a", db_path=db) is False
    assert {p.name for p in list_profiles(db_path=db)} == {"b"}


def test_get_missing_profile(tmp_path: Any) -> None:
    assert get_profile("nope", db_path=tmp_path / "halia.db") is None


def test_build_registry_always_includes_calculate() -> None:
    # A marketing profile with no math skills still gets calculate (trust floor).
    registry = build_registry(["fetch_url"])
    assert registry.get("calculate") is not None
    assert registry.get("fetch_url") is not None


def test_build_registry_selects_subset_and_skips_unknown() -> None:
    registry = build_registry(["read_csv", "bogus_skill"])
    assert registry.get("read_csv") is not None
    assert registry.get("bogus_skill") is None
    # a skill NOT in the profile isn't there
    assert registry.get("run_command") is None
