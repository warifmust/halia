"""Tests for the CLI approver's trust-this-directory scope."""

from typing import Any

import halia.cli.main as main
from halia.cli.main import _make_approver, _write_target_dir


def test_write_target_dir_resolves_write_file() -> None:
    d = _write_target_dir("write_file", '{"path": "/tmp/reports/out.txt", "content": "x"}')
    assert d == "/tmp/reports"


def test_write_target_dir_none_for_other_tools() -> None:
    assert _write_target_dir("run_command", '{"command": "ls"}') is None
    assert _write_target_dir("write_file", "not json") is None
    assert _write_target_dir("write_file", "{}") is None  # no path


def test_trusting_a_dir_skips_reprompt(monkeypatch: Any) -> None:
    prompts: list[str] = []

    def fake_input(_prompt: str = "") -> str:
        prompts.append(_prompt)
        return "a"  # user chooses "all writes to this folder"

    monkeypatch.setattr(main.console, "input", fake_input)
    approve = _make_approver()

    # first write to /tmp/reports → prompts, user trusts the folder
    assert approve("write_file", '{"path": "/tmp/reports/a.txt", "content": "1"}') is True
    # second write to the SAME folder → auto-approved, no new prompt
    assert approve("write_file", '{"path": "/tmp/reports/b.txt", "content": "2"}') is True
    assert len(prompts) == 1  # only the first call prompted

    # a DIFFERENT folder is still gated (prompts again)
    assert approve("write_file", '{"path": "/tmp/other/c.txt", "content": "3"}') is True
    assert len(prompts) == 2


def test_deny_choice_blocks(monkeypatch: Any) -> None:
    monkeypatch.setattr(main.console, "input", lambda _p="": "n")
    approve = _make_approver()
    assert approve("write_file", '{"path": "/tmp/x/a.txt", "content": "1"}') is False


def test_yes_choice_is_one_shot(monkeypatch: Any) -> None:
    calls: list[str] = []

    def fake_input(_prompt: str = "") -> str:
        calls.append(_prompt)
        return "y"  # approve once, do NOT trust the folder

    monkeypatch.setattr(main.console, "input", fake_input)
    approve = _make_approver()
    assert approve("write_file", '{"path": "/tmp/x/a.txt", "content": "1"}') is True
    assert approve("write_file", '{"path": "/tmp/x/b.txt", "content": "2"}') is True
    assert len(calls) == 2  # each write re-prompted (folder was never trusted)
