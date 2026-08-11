"""Tests for the read-only skills and the permission floor."""

from pathlib import Path
from typing import Any

import pytest

from halia.permissions.guard import PermissionDenied, check_readable
from halia.skills.exec import RunCommand
from halia.skills.fs import ListFiles, ReadFile, WriteFile


def test_read_file(tmp_path: Any) -> None:
    target = tmp_path / "a.txt"
    target.write_text("hello")
    assert ReadFile().run({"path": str(target)}) == "hello"


def test_read_file_pages_large_file_with_offset_limit(tmp_path: Any) -> None:
    target = tmp_path / "big.txt"
    target.write_text("".join(f"line{i}\n" for i in range(1, 101)))  # 100 lines

    # limit → first N lines + a continue footer
    out = ReadFile().run({"path": str(target), "limit": 10})
    assert "line1\n" in out and "line10\n" in out
    assert "line11" not in out.split("[")[0]  # line 11 not in the body
    assert "lines 1–10 of 100" in out and "offset=11 to continue" in out

    # offset → resume from a later line
    out2 = ReadFile().run({"path": str(target), "offset": 11, "limit": 5})
    assert "line11\n" in out2 and "line15\n" in out2
    assert "line10\n" not in out2

    # offset past EOF → clear error
    assert "past end of file" in ReadFile().run({"path": str(target), "offset": 999})

    # string-typed numbers (models pass these) are coerced
    out3 = ReadFile().run({"path": str(target), "offset": "11", "limit": "3"})
    assert "line11\n" in out3


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


def test_write_file(tmp_path: Any) -> None:
    target = tmp_path / "out.txt"
    out = WriteFile().run({"path": str(target), "content": "hello world"})
    assert "wrote 11 chars" in out
    assert target.read_text() == "hello world"


def test_write_file_creates_parent_dirs(tmp_path: Any) -> None:
    target = tmp_path / "sub" / "deep" / "out.txt"
    WriteFile().run({"path": str(target), "content": "x"})
    assert target.read_text() == "x"


def test_write_file_blocked_sensitive(tmp_path: Any) -> None:
    with pytest.raises(PermissionDenied):
        WriteFile().run({"path": str(tmp_path / "config.env"), "content": "x"})


def test_write_file_is_dangerous() -> None:
    assert WriteFile().dangerous is True


def test_write_file_requires_args() -> None:
    assert "required" in WriteFile().run({"content": "x"})
    assert "required" in WriteFile().run({"path": "x.txt"})
