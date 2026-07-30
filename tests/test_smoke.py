"""Smoke tests for the halia package."""

from halia import __version__


def test_version_is_set() -> None:
    assert __version__
