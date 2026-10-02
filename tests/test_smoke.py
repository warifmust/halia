"""Smoke tests for the halia package."""

import re
from pathlib import Path

from halia import __version__

_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_version_is_set() -> None:
    assert __version__


def test_readme_banner_matches_version() -> None:
    """The README's ASCII banner version stays in sync with halia.__version__."""
    readme = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")
    match = re.search(r"^╭─ v(\d+\.\d+\.\d+) ─+╮$", readme, re.MULTILINE)
    assert match is not None, "README banner missing a `╭─ vX.Y.Z ─…─╮` line"
    assert match.group(1) == __version__
