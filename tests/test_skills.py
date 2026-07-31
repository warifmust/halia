"""Tests for the read-only skills and the permission floor."""

from pathlib import Path
from typing import Any

import pytest

from halia.permissions.guard import PermissionDenied, check_readable
from halia.skills.exec import RunCommand
from halia.skills.fs import ListFiles, ReadFile


def test_read_file(tmp_path: Any) -> None:
    target = tmp_path / "a.txt"
    target.write_text("hello")
    assert ReadFile().run({"path": str(target)}) == "hello"


def test_read_file_missing(tmp_path: Any) -> None:
    out = ReadFile().run({"path": str(tmp_path / "nope.txt")})
    assert "not a file" in out


def test_read_file_requires_path() -> None:
    assert "required" in ReadFile().run({})


def test_list_files(tmp_path: Any) -> None:
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "sub").mkdir()
    out = ListFiles().run({"path": str(tmp_path)})
    assert "a.txt" in out
    assert "sub/" in out


def test_permission_floor_blocks_ssh() -> None:
    with pytest.raises(PermissionDenied):
        check_readable(Path.home() / ".ssh" / "id_rsa")


def test_permission_floor_blocks_env(tmp_path: Any) -> None:
    with pytest.raises(PermissionDenied):
        check_readable(tmp_path / ".env")


def test_run_command_echo() -> None:
    out = RunCommand().run({"command": "echo hello"})
    assert "exit_code: 0" in out
    assert "hello" in out


def test_run_command_requires_command() -> None:
    assert "required" in RunCommand().run({})


def test_run_command_is_dangerous() -> None:
    assert RunCommand().dangerous is True
