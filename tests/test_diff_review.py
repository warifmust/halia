"""Tests for diff review before file writes."""

from __future__ import annotations

import json
from pathlib import Path

from halia.cli.main import _generate_diff


def test_diff_for_existing_file(tmp_path: Path) -> None:
    """Generates a coloured unified diff for an existing file."""
    target = tmp_path / "hello.txt"
    target.write_text("line 1\nline 2\nline 3\n")

    args = json.dumps({"path": str(target), "content": "line 1\nCHANGED\nline 3\n"})
    diff = _generate_diff("write_file", args)

    assert diff is not None
    assert "line 2" in diff  # removed line
    assert "CHANGED" in diff  # added line
    assert "[red]" in diff  # removals are red
    assert "[green]" in diff  # additions are green


def test_diff_for_new_file(tmp_path: Path) -> None:
    """No diff for a new file (doesn't exist yet)."""
    target = tmp_path / "new.txt"
    args = json.dumps({"path": str(target), "content": "hello\n"})
    assert _generate_diff("write_file", args) is None


def test_diff_for_no_changes(tmp_path: Path) -> None:
    """Shows '(no changes)' when content is identical."""
    target = tmp_path / "hello.txt"
    target.write_text("same\n")

    args = json.dumps({"path": str(target), "content": "same\n"})
    diff = _generate_diff("write_file", args)

    assert diff is not None
    assert "no changes" in diff


def test_diff_for_non_write_tool() -> None:
    """Returns None for tools that aren't write_file."""
    args = json.dumps({"path": "/tmp/x.txt", "content": "hi"})
    assert _generate_diff("read_file", args) is None
    assert _generate_diff("make_chart", args) is None


def test_diff_for_invalid_json() -> None:
    """Returns None for malformed arguments."""
    assert _generate_diff("write_file", "not json") is None


def test_diff_for_missing_path() -> None:
    """Returns None when path is missing from arguments."""
    args = json.dumps({"content": "hello"})
    assert _generate_diff("write_file", args) is None


def test_diff_shows_file_header(tmp_path: Path) -> None:
    """Diff includes the filename in the header."""
    target = tmp_path / "data.csv"
    target.write_text("old\n")

    args = json.dumps({"path": str(target), "content": "new\n"})
    diff = _generate_diff("write_file", args)

    assert diff is not None
    assert "data.csv" in diff
