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

    def fake_pick(title: str = "", options: list[str] | None = None, default: int = 0) -> str:
        prompts.append(title)
        return options[1] if options else "all"  # user chooses "all writes to this folder"

    monkeypatch.setattr("halia.cli.input.pick", fake_pick)
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
    def fake_pick(title: str = "", options: list[str] | None = None, default: int = 0) -> str:
        return options[2] if options else "no"  # user chooses "no"

    monkeypatch.setattr("halia.cli.input.pick", fake_pick)
    approve = _make_approver()
    assert approve("write_file", '{"path": "/tmp/x/a.txt", "content": "1"}') is False


def test_cua_batch_grant_trusts_all_cua_tools(monkeypatch: Any) -> None:
    """Granting full CUA control on the first cua_* call skips prompts for all others."""
    prompts: list[str] = []

    def fake_pick(title: str = "", options: list[str] | None = None, default: int = 0) -> str:
        prompts.append(title)
        # First prompt is the CUA batch gate; user grants full control.
        return options[0] if options else "yes"

    monkeypatch.setattr("halia.cli.input.pick", fake_pick)
    approve = _make_approver()

    # First CUA action prompts for full control.
    assert approve("cua_open_url", '{"url": "https://example.com"}') is True
    # Subsequent CUA actions are auto-approved without re-prompting.
    assert approve("cua_click", '{"x": 100, "y": 200}') is True
    assert approve("cua_type", '{"text": "hello"}') is True
    assert approve("cua_screenshot", "{}") is True
    assert len(prompts) == 1


def test_cua_batch_deny_falls_back_to_per_call(monkeypatch: Any) -> None:
    """Denying full CUA control falls back to per-action approval."""
    prompts: list[str] = []

    def fake_pick(title: str = "", options: list[str] | None = None, default: int = 0) -> str:
        prompts.append(title)
        # First prompt: CUA batch gate → deny. Then per-call prompts → approve once.
        if len(prompts) == 1:
            return options[1] if options else "no"
        return options[0] if options else "yes"

    monkeypatch.setattr("halia.cli.input.pick", fake_pick)
    approve = _make_approver()

    # First CUA action: batch gate denied, then per-call prompt approved.
    assert approve("cua_open_url", '{"url": "https://example.com"}') is True
    # Next CUA action: batch gate already declined, so per-call prompt again.
    assert approve("cua_click", '{"x": 100, "y": 200}') is True
    assert len(prompts) == 3  # batch gate + 2 per-call approvals


def test_non_cua_tools_do_not_trigger_cua_batch(monkeypatch: Any) -> None:
    """Regular tools should not prompt for CUA batch control."""
    prompts: list[str] = []

    def fake_pick(title: str = "", options: list[str] | None = None, default: int = 0) -> str:
        prompts.append(title)
        return options[0] if options else "yes"

    monkeypatch.setattr("halia.cli.input.pick", fake_pick)
    approve = _make_approver()

    assert approve("write_file", '{"path": "/tmp/x/a.txt", "content": "1"}') is True
    assert len(prompts) == 1
    assert "CUA" not in prompts[0]


def test_browser_consent_granted_once(monkeypatch: Any) -> None:
    """Granting full browser control on the first browser action covers the rest."""
    prompts: list[str] = []

    def fake_pick(title: str = "", options: list[str] | None = None, default: int = 0) -> str:
        prompts.append(title)
        return options[0] if options else "yes"

    monkeypatch.setattr("halia.cli.input.pick", fake_pick)
    approve = _make_approver()

    assert approve.check_consent("browser_open") is True
    assert approve.check_consent("browser_click") is True
    assert approve.check_consent("browser_type") is True
    assert approve.check_consent("browser_screenshot") is True
    assert len(prompts) == 1  # asked once, then trusted for the session


def test_browser_consent_denied_persists(monkeypatch: Any) -> None:
    """Declining full browser control blocks browser tools without re-prompting."""
    prompts: list[str] = []

    def fake_pick(title: str = "", options: list[str] | None = None, default: int = 0) -> str:
        prompts.append(title)
        return options[1] if options else "no"

    monkeypatch.setattr("halia.cli.input.pick", fake_pick)
    approve = _make_approver()

    assert approve.check_consent("browser_open") is False
    assert approve.check_consent("browser_read") is False
    assert len(prompts) == 1  # asked once, then cached decline


def test_non_browser_tools_skip_consent(monkeypatch: Any) -> None:
    """Only browser_* tools trigger the browser consent gate."""
    prompts: list[str] = []

    def fake_pick(title: str = "", options: list[str] | None = None, default: int = 0) -> str:
        prompts.append(title)
        return options[0] if options else "yes"

    monkeypatch.setattr("halia.cli.input.pick", fake_pick)
    approve = _make_approver()

    assert approve.check_consent("write_file") is True
    assert approve.check_consent("cua_open_url") is True
    assert approve.check_consent("http_request") is True
    assert len(prompts) == 0  # no consent prompt for non-browser tools


def test_browser_consent_does_not_grant_cua(monkeypatch: Any) -> None:
    """Granting browser control must NOT auto-grant desktop (CUA) control."""
    prompts: list[str] = []

    def fake_pick(title: str = "", options: list[str] | None = None, default: int = 0) -> str:
        prompts.append(title)
        return options[0] if options else "yes"

    monkeypatch.setattr("halia.cli.input.pick", fake_pick)
    approve = _make_approver()

    # Grant browser consent.
    assert approve.check_consent("browser_open") is True
    # CUA is a different gate — it must still prompt (and grant on its own).
    assert approve("cua_click", '{"x": 100, "y": 200}') is True
    assert len(prompts) == 2  # browser consent + cua consent


def test_cua_consent_does_not_grant_browser(monkeypatch: Any) -> None:
    """Granting desktop (CUA) control must NOT auto-grant browser control."""
    prompts: list[str] = []

    def fake_pick(title: str = "", options: list[str] | None = None, default: int = 0) -> str:
        prompts.append(title)
        return options[0] if options else "yes"

    monkeypatch.setattr("halia.cli.input.pick", fake_pick)
    approve = _make_approver()

    # Grant CUA consent (first dangerous cua_* call).
    assert approve("cua_open_url", '{"url": "https://example.com"}') is True
    assert len(prompts) == 1  # CUA gate only
    # Browser consent is a different gate — it must still prompt.
    assert approve.check_consent("browser_open") is True
    assert len(prompts) == 2  # cua consent + browser consent

