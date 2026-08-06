"""Tests for the CLI approver's trust-this-directory scope."""

from typing import Any

from halia.cli.main import _make_approver, _write_target_dir


def test_write_target_dir_resolves_write_file() -> None:
    d = _write_target_dir("write_file", '{"path": "/tmp/reports/out.txt", "content": "x"}')
    assert d == "/tmp/reports"


def test_write_target_dir_covers_any_path_writer() -> None:
    # not just write_file — make_chart and any future path-writing tool too
    assert _write_target_dir("make_chart", '{"path": "/tmp/out/c.svg"}') == "/tmp/out"


def test_write_target_dir_none_for_other_tools() -> None:
    assert _write_target_dir("run_command", '{"command": "ls"}') is None
    assert _write_target_dir("write_file", "not json") is None
    assert _write_target_dir("write_file", "{}") is None  # no path


def test_trusting_a_dir_skips_reprompt(monkeypatch: Any) -> None:
    prompts: list[str] = []

    def fake_ask(prompt_text: str = "", default: str = "", is_password: bool = False) -> str:
        prompts.append(prompt_text)
        return "a"  # user chooses "all writes to this folder"

    monkeypatch.setattr("halia.cli.input.ask", fake_ask)
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
    monkeypatch.setattr("halia.cli.input.ask", lambda *a, **kw: "n")
    approve = _make_approver()
    assert approve("write_file", '{"path": "/tmp/x/a.txt", "content": "1"}') is False


def test_yes_choice_is_one_shot(monkeypatch: Any) -> None:
    calls: list[str] = []

    def fake_ask(prompt_text: str = "", default: str = "", is_password: bool = False) -> str:
        calls.append(prompt_text)
        return "y"  # approve once, do NOT trust the folder

    monkeypatch.setattr("halia.cli.input.ask", fake_ask)
    approve = _make_approver()
    assert approve("write_file", '{"path": "/tmp/x/a.txt", "content": "1"}') is True
    assert approve("write_file", '{"path": "/tmp/x/b.txt", "content": "2"}') is True
    assert len(calls) == 2  # each write re-prompted (folder was never trusted)
