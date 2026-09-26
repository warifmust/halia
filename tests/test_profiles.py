"""Tests for registry building (the trust floor)."""

from halia.skills import build_registry


def test_build_registry_always_includes_calculate() -> None:
    # any skill subset still gets calculate (the trust floor).
    registry = build_registry(["fetch_url"])
    assert registry.get("calculate") is not None
    assert registry.get("fetch_url") is not None


def test_build_registry_selects_subset_and_skips_unknown() -> None:
    registry = build_registry(["read_csv", "bogus_skill"])
    assert registry.get("read_csv") is not None
    assert registry.get("bogus_skill") is None
    # a skill NOT in the profile isn't there
    assert registry.get("run_command") is None
