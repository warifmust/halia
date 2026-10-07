"""Tests for the cua-driver version lock.

cua-driver 0.34.0 added a required `cursor_motion` field to StartSessionInput,
which halia does not pass, so a driver outside the supported range breaks every
cua_* tool at session start. Three things keep that from happening silently: the
pin, a session-start guard, and an upgrade path that re-applies the cap.
"""

from __future__ import annotations

import asyncio
import json
import types
from pathlib import Path
from typing import Any

import pytest

from halia.computer import cua_backend


def test_supported_driver_range() -> None:
    """Every release halia was tested against is accepted; 0.34.0+ is not."""
    for version in ("0.29", "0.29.1", "0.30.1", "0.31.0", "0.33.4"):
        assert cua_backend.cua_driver_supported(version), version
    for version in ("0.28.9", "0.34.0", "0.34.1", "1.0.0"):
        assert not cua_backend.cua_driver_supported(version), version


def test_check_reports_nothing_when_supported(monkeypatch: Any) -> None:
    monkeypatch.setattr(cua_backend, "runtime_cua_driver_version", lambda: "0.29.1")
    assert cua_backend.check_cua_driver_version() is None


def test_check_silent_when_driver_absent(monkeypatch: Any) -> None:
    """CUA is optional — an absent driver is not a version problem."""
    monkeypatch.setattr(cua_backend, "runtime_cua_driver_version", lambda: None)
    assert cua_backend.check_cua_driver_version() is None


def test_check_names_the_repair_command(monkeypatch: Any) -> None:
    monkeypatch.setattr(cua_backend, "runtime_cua_driver_version", lambda: "0.34.0")
    message = cua_backend.check_cua_driver_version()
    assert message is not None
    assert "0.34.0" in message
    assert "cursor_motion" in message
    # Actionable: the exact command, with the interpreter to install into.
    assert "uv pip install --python" in message
    assert f"cua-driver{cua_backend.CUA_DRIVER_SPEC}" in message


def test_session_start_refuses_an_unsupported_driver(monkeypatch: Any) -> None:
    """The guard must fire before start_session, so the error is never a bare TypeError."""
    from halia.computer.cua_backend import CuaComputer

    class FakeDriver:
        async def start_session(self, _input: Any) -> None:  # pragma: no cover
            raise AssertionError("start_session must not be reached")

    cua = CuaComputer()
    cua._driver = FakeDriver()
    cua._session_started = False
    monkeypatch.setattr(
        cua_backend, "check_cua_driver_version", lambda: "driver 0.34.0 is too new"
    )

    with pytest.raises(RuntimeError, match="too new"):
        asyncio.run(cua._ensure_session())


def test_session_start_allows_a_supported_driver(monkeypatch: Any) -> None:
    """The guard must not block a supported driver — the fake SDK accepts it."""
    from halia.computer.cua_backend import CuaComputer

    started: list[Any] = []

    class FakeDriver:
        async def start_session(self, _input: Any) -> None:
            started.append(_input)

        async def set_agent_cursor_enabled(self, _input: Any) -> None:
            pass

    sdk = types.ModuleType("cua_driver")

    class StartSessionInput:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    class SetAgentCursorEnabledInput:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    class CursorReducedMotion:
        AUTO = "auto"
        ON = "on"
        OFF = "off"

    class CursorThemeSelection:
        def __init__(self, *, theme_id: str, reduced_motion: Any) -> None:
            self.theme_id = theme_id
            self.reduced_motion = reduced_motion

    sdk.StartSessionInput = StartSessionInput  # type: ignore[attr-defined]
    sdk.SetAgentCursorEnabledInput = SetAgentCursorEnabledInput  # type: ignore[attr-defined]
    sdk.CursorReducedMotion = CursorReducedMotion  # type: ignore[attr-defined]
    sdk.CursorThemeSelection = CursorThemeSelection  # type: ignore[attr-defined]
    monkeypatch.setitem(__import__("sys").modules, "cua_driver", sdk)
    monkeypatch.setattr(cua_backend, "check_cua_driver_version", lambda: None)

    cua = CuaComputer()
    cua._driver = FakeDriver()
    cua._session_started = False

    asyncio.run(cua._ensure_session())
    assert len(started) == 1


# ── upgrade preserves the lock ────────────────────────────────────────────


def _write_receipt(tmp_path: Path, body: str) -> Path:
    tool_dir = tmp_path / "tools"
    (tool_dir / "halia").mkdir(parents=True)
    (tool_dir / "halia" / "uv-receipt.toml").write_text(body, encoding="utf-8")
    return tool_dir


def test_installed_extra_requirements_reads_the_receipt(tmp_path: Path) -> None:
    from halia.upgrade import installed_extra_requirements

    tool_dir = _write_receipt(
        tmp_path,
        """
[tool]
requirements = [
    { name = "halia", git = "https://github.com/warifmust/halia.git?rev=main" },
    { name = "mcp" },
    { name = "cua-driver", specifier = ">=0.29,<0.34" },
    { name = "frompath", path = "/tmp/local" },
]
""",
    )
    # halia itself is the target, not a --with; a path requirement is not emitted.
    assert installed_extra_requirements(str(tool_dir)) == [
        "mcp",
        "cua-driver>=0.29,<0.34",
    ]


def test_installed_extra_requirements_is_empty_without_a_receipt(tmp_path: Path) -> None:
    from halia.upgrade import installed_extra_requirements

    assert installed_extra_requirements(str(tmp_path / "nope")) == []


def test_installed_extra_requirements_survives_a_broken_receipt(tmp_path: Path) -> None:
    from halia.upgrade import installed_extra_requirements

    tool_dir = _write_receipt(tmp_path, "this is not toml {{{")
    assert installed_extra_requirements(str(tool_dir)) == []


def test_upgrade_reapplies_the_cap_and_drops_a_recorded_driver(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """An upgrade must never carry an unsupported driver forward."""
    from halia import upgrade

    tool_dir = _write_receipt(
        tmp_path,
        """
[tool]
requirements = [
    { name = "halia", git = "https://github.com/warifmust/halia.git?rev=main" },
    { name = "mcp" },
    { name = "cua-driver", specifier = ">=0.29" },
]
""",
    )
    monkeypatch.setattr(upgrade, "_tool_dir", lambda: str(tool_dir))
    monkeypatch.setattr(
        cua_backend, "runtime_cua_driver_version", lambda: "0.29.1"
    )

    # The loose recorded spec is replaced by the supported range, not kept.
    assert upgrade.upgrade_with_requirements() == [
        f"cua-driver{cua_backend.CUA_DRIVER_SPEC}",
        "mcp",
    ]


def test_upgrade_skips_the_driver_when_cua_is_absent(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """No CUA installed → do not pull a 200MB driver into an upgrade."""
    from halia import upgrade

    tool_dir = _write_receipt(
        tmp_path,
        """
[tool]
requirements = [
    { name = "halia", git = "https://github.com/warifmust/halia.git?rev=main" },
    { name = "mcp" },
]
""",
    )
    monkeypatch.setattr(upgrade, "_tool_dir", lambda: str(tool_dir))
    monkeypatch.setattr(cua_backend, "runtime_cua_driver_version", lambda: None)

    assert upgrade.upgrade_with_requirements() == ["mcp"]


def test_perform_upgrade_passes_the_extras_through(monkeypatch: Any) -> None:
    """The uv command must carry every --with, since --force rebuilds from it alone."""
    from halia import upgrade

    captured: dict[str, Any] = {}

    class FakeResult:
        returncode = 0
        stdout = "installed"
        stderr = ""

    def fake_run(command: list[str], **kwargs: Any) -> FakeResult:
        captured["command"] = command
        return FakeResult()

    monkeypatch.setattr(upgrade.shutil, "which", lambda _name: "/usr/bin/uv")
    monkeypatch.setattr(
        upgrade, "upgrade_with_requirements", lambda: ["cua-driver>=0.29,<0.34", "mcp"]
    )
    monkeypatch.setattr(upgrade.subprocess, "run", fake_run)

    ok, detail = upgrade.perform_upgrade()

    assert ok is True
    assert detail == "installed"
    command = captured["command"]
    assert command[:4] == ["/usr/bin/uv", "tool", "install", "--force"]
    assert "--with" in command
    assert "cua-driver>=0.29,<0.34" in command
    assert "mcp" in command


# ── doctor surfaces the lock ──────────────────────────────────────────────


def test_doctor_reports_a_supported_driver(monkeypatch: Any) -> None:
    from halia import doctor

    monkeypatch.setattr(cua_backend, "runtime_cua_driver_version", lambda: "0.29.1")
    check = doctor._cua_driver()
    assert check.status == doctor.OK
    assert "0.29.1" in check.detail


def test_doctor_fails_on_an_unsupported_driver(monkeypatch: Any) -> None:
    from halia import doctor

    monkeypatch.setattr(cua_backend, "runtime_cua_driver_version", lambda: "0.34.0")
    check = doctor._cua_driver()
    assert check.status == doctor.FAIL
    assert "halia setup --cua" in check.detail


def test_doctor_treats_an_absent_driver_as_ok(monkeypatch: Any) -> None:
    from halia import doctor

    monkeypatch.setattr(cua_backend, "runtime_cua_driver_version", lambda: None)
    check = doctor._cua_driver()
    assert check.status == doctor.OK
    assert "optional" in check.detail


def test_doctor_check_is_wired_in() -> None:
    from halia import doctor

    assert "_cua_driver" in {fn.__name__ for fn in doctor._CHECKS}
    assert json.dumps([c.name for c in doctor.run_checks()])


def test_wizard_pin_matches_the_backend_range() -> None:
    """The installer and the guard must never disagree about supported versions."""
    from halia.config.wizard import _cua_driver_pin

    assert _cua_driver_pin() == f"cua-driver{cua_backend.CUA_DRIVER_SPEC}"


def test_setup_installs_a_pinned_driver(monkeypatch: Any) -> None:
    """`halia setup --cua` must not install an unpinned (upgradeable) driver."""
    from halia.config import wizard

    calls: list[str] = []

    def fake_install(_console: Any, package: str, **kwargs: Any) -> bool:
        calls.append(package)
        return False  # stop before the permission/verification steps

    class FakeConsole:
        def print(self, *_a: Any, **_k: Any) -> None:
            pass

    monkeypatch.setattr(wizard, "_install_python_package", fake_install)
    wizard._install_cua_driver(FakeConsole())

    assert calls == [f"cua-driver{cua_backend.CUA_DRIVER_SPEC}"]
    # An exact pin, not an open or ranged spec: nothing may re-resolve CUA to a
    # driver build halia has not been validated against.
    assert calls[0] == f"cua-driver=={cua_backend.CUA_DRIVER_SPEC.lstrip('=')}"
    assert "<" not in calls[0] and ">" not in calls[0]


def test_upgrade_restores_a_driver_that_went_missing(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A driver dropped from the env is restored on upgrade while the receipt records it.

    This is the recovery path for a stripped install: the package is gone, so the
    'is it installed' trigger cannot fire — the receipt is the only remaining
    evidence that the user wanted CUA.
    """
    from halia import upgrade

    tool_dir = _write_receipt(
        tmp_path,
        """
[tool]
requirements = [
    { name = "halia", directory = "/tmp/halia" },
    { name = "mcp" },
    { name = "cua-driver", specifier = "==0.29.1" },
]
""",
    )
    monkeypatch.setattr(upgrade, "_tool_dir", lambda: str(tool_dir))
    monkeypatch.setattr(cua_backend, "runtime_cua_driver_version", lambda: None)

    assert upgrade.upgrade_with_requirements() == [
        f"cua-driver{cua_backend.CUA_DRIVER_SPEC}",
        "mcp",
    ]


def test_upgrade_does_not_resurrect_a_driver_the_user_removed(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """No driver installed AND none recorded → leave CUA off."""
    from halia import upgrade

    tool_dir = _write_receipt(
        tmp_path,
        """
[tool]
requirements = [
    { name = "halia", directory = "/tmp/halia" },
    { name = "mcp" },
]
""",
    )
    monkeypatch.setattr(upgrade, "_tool_dir", lambda: str(tool_dir))
    monkeypatch.setattr(cua_backend, "runtime_cua_driver_version", lambda: None)

    assert upgrade.upgrade_with_requirements() == ["mcp"]


# ── the durable record (outlives `uv tool install --force`) ────────────────


def test_declared_extras_reads_the_record() -> None:
    from halia.config.settings import write_config
    from halia.upgrade import declared_extras

    write_config({"tool_extras": ["mcp", "cua-driver==0.29.1"]})
    assert declared_extras() == ["mcp", "cua-driver==0.29.1"]


def test_declared_extras_tolerates_an_unusable_record() -> None:
    from halia.config.settings import write_config
    from halia.upgrade import declared_extras

    for value in ("mcp", None, {"mcp": True}):
        write_config({"tool_extras": value})
        assert declared_extras() == []
    write_config({"tool_extras": ["mcp", 7, "", " "]})
    assert declared_extras() == ["mcp"]


def test_remember_extras_merges_without_clobbering_the_config() -> None:
    """The record is merged into the user's config, not a rewrite of it."""
    from halia.config.settings import read_config, write_config
    from halia.upgrade import remember_extras

    write_config({"trusted_dirs": ["/tmp/work"], "provider": "deepseek"})
    remember_extras(["cua-driver==0.29.1"])
    remember_extras(["mcp"])

    data = read_config()
    assert data["trusted_dirs"] == ["/tmp/work"]
    assert data["provider"] == "deepseek"
    assert data["tool_extras"] == ["cua-driver==0.29.1", "mcp"]


def test_remember_extras_prefers_a_pinned_spec() -> None:
    from halia.upgrade import declared_extras, remember_extras

    remember_extras(["mcp"])
    remember_extras(["mcp>=1"])
    assert declared_extras() == ["mcp>=1"]
    # A later bare name must not downgrade the pin.
    remember_extras(["mcp"])
    assert declared_extras() == ["mcp>=1"]


def test_upgrade_reapplies_from_the_record_after_a_strip(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The reason the record exists.

    One bare `uv tool install --force` leaves the receipt recording only halia,
    so the receipt alone cannot restore mcp or the driver. The config can.
    """
    from halia import upgrade
    from halia.config.settings import write_config

    tool_dir = _write_receipt(
        tmp_path,
        """
[tool]
requirements = [
    { name = "halia", directory = "/tmp/halia" },
]
""",
    )
    monkeypatch.setattr(upgrade, "_tool_dir", lambda: str(tool_dir))
    monkeypatch.setattr(cua_backend, "runtime_cua_driver_version", lambda: None)
    write_config({"tool_extras": ["mcp", "cua-driver==0.29.1"]})

    assert upgrade.upgrade_with_requirements() == [
        f"cua-driver{cua_backend.CUA_DRIVER_SPEC}",
        "mcp",
    ]


def test_perform_upgrade_records_what_it_installed(monkeypatch: Any) -> None:
    """Seeding: an install predating the record populates it for next time."""
    from halia import upgrade
    from halia.upgrade import declared_extras

    class FakeResult:
        returncode = 0
        stdout = "installed"
        stderr = ""

    monkeypatch.setattr(upgrade.shutil, "which", lambda _name: "/usr/bin/uv")
    monkeypatch.setattr(
        upgrade,
        "upgrade_with_requirements",
        lambda: ["cua-driver==0.29.1", "mcp"],
    )
    monkeypatch.setattr(upgrade.subprocess, "run", lambda *a, **k: FakeResult())

    ok, _ = upgrade.perform_upgrade()

    assert ok is True
    assert declared_extras() == ["cua-driver==0.29.1", "mcp"]


def test_add_extras_records_only_what_installed(monkeypatch: Any) -> None:
    from halia import upgrade
    from halia.upgrade import declared_extras

    monkeypatch.setattr(
        upgrade, "install_requirements", lambda specs, timeout=300: (True, "")
    )
    assert upgrade.add_extras(["mcp"]) == (True, "")
    assert declared_extras() == ["mcp"]

    monkeypatch.setattr(
        upgrade, "install_requirements", lambda specs, timeout=300: (False, "boom")
    )
    assert upgrade.add_extras(["nope"]) == (False, "boom")
    assert declared_extras() == ["mcp"]


def test_install_requirements_never_reinstalls_the_tool(monkeypatch: Any) -> None:
    """An on-demand extra must not rewrite the tool receipt halia was built with."""
    from halia import upgrade

    captured: dict[str, Any] = {}

    class FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command: list[str], **kwargs: Any) -> FakeResult:
        captured["command"] = command
        return FakeResult()

    monkeypatch.setattr(upgrade.shutil, "which", lambda _name: "/usr/bin/uv")
    monkeypatch.setattr(upgrade.subprocess, "run", fake_run)

    ok, _ = upgrade.install_requirements(["mcp"])

    assert ok is True
    command = captured["command"]
    assert command[1:3] == ["pip", "install"]
    assert "tool" not in command
    assert "mcp" in command


def test_setup_mcp_choice_installs_the_package(monkeypatch: Any) -> None:
    """Choosing 'configure MCP' must install `mcp`, not only write mcp.json."""
    from halia.config import wizard

    calls: list[str] = []

    class FakeConsole:
        def print(self, *_a: Any, **_k: Any) -> None:
            pass

    def fake_install(_console: Any, package: str, **kwargs: Any) -> bool:
        calls.append(package)
        return True

    monkeypatch.setattr(wizard, "_install_python_package", fake_install)
    monkeypatch.setattr("halia.mcp.mcp_available", lambda: False)

    wizard._enable_mcp(FakeConsole())

    assert calls == ["mcp"]


def test_setup_mcp_choice_is_skipped_when_already_installed(monkeypatch: Any) -> None:
    from halia.config import wizard

    class FakeConsole:
        def print(self, *_a: Any, **_k: Any) -> None:
            pass

    monkeypatch.setattr(
        wizard, "_install_python_package", lambda *a, **k: pytest.fail("reinstalled")
    )
    monkeypatch.setattr("halia.mcp.mcp_available", lambda: True)

    wizard._enable_mcp(FakeConsole())


def test_wizard_install_records_the_extra(monkeypatch: Any) -> None:
    """The wizard's installer is the recording path for CUA and MCP."""
    from halia.config import wizard

    seen: list[list[str]] = []

    class FakeConsole:
        def print(self, *_a: Any, **_k: Any) -> None:
            pass

    def fake_add(specs: list[str], timeout: int = 300) -> tuple[bool, str]:
        seen.append(specs)
        return (True, "")

    monkeypatch.setattr("halia.upgrade.add_extras", fake_add)

    assert wizard._install_python_package(FakeConsole(), "mcp") is True
    assert seen == [["mcp"]]


def test_doctor_flags_a_recorded_extra_that_went_missing(monkeypatch: Any) -> None:
    """Detects the strip: the record survives, the environment does not."""
    from halia import doctor
    from halia.config.settings import write_config

    write_config({"tool_extras": ["mcp", "cua-driver==0.29.1"]})
    monkeypatch.setattr(
        "halia.upgrade.is_installed", lambda name: name == "cua-driver"
    )

    check = doctor._extras()

    assert check.status == doctor.FAIL
    assert "mcp" in check.detail
    assert "halia upgrade" in check.detail


def test_doctor_is_happy_when_recorded_extras_are_present(monkeypatch: Any) -> None:
    from halia import doctor
    from halia.config.settings import write_config

    write_config({"tool_extras": ["mcp", "cua-driver==0.29.1"]})
    monkeypatch.setattr("halia.upgrade.is_installed", lambda _name: True)

    check = doctor._extras()

    assert check.status == doctor.OK
    assert "mcp" in check.detail


def test_doctor_reports_no_record_as_fine() -> None:
    from halia import doctor

    check = doctor._extras()

    assert check.status == doctor.OK


# ── an up-to-date version is not the same as a healthy environment ─────────


def test_missing_declared_extras_names_only_what_is_gone(monkeypatch: Any) -> None:
    from halia.config.settings import write_config
    from halia.upgrade import missing_declared_extras

    write_config({"tool_extras": ["mcp", "cua-driver==0.29.1"]})
    monkeypatch.setattr("halia.upgrade.is_installed", lambda name: name == "mcp")

    assert missing_declared_extras() == ["cua-driver==0.29.1"]


def test_repair_extras_installs_only_what_is_missing(monkeypatch: Any) -> None:
    from halia import upgrade
    from halia.config.settings import write_config

    write_config({"tool_extras": ["mcp", "cua-driver==0.29.1"]})
    monkeypatch.setattr("halia.upgrade.is_installed", lambda name: name == "cua-driver")
    seen: list[list[str]] = []
    monkeypatch.setattr(
        upgrade,
        "install_requirements",
        lambda specs, timeout=300: (seen.append(list(specs)), (True, ""))[1],
    )

    ok, detail = upgrade.repair_extras()

    assert ok is True
    assert seen == [["mcp"]]
    assert detail == "mcp"


def test_repair_extras_does_nothing_when_healthy(monkeypatch: Any) -> None:
    from halia import upgrade
    from halia.config.settings import write_config

    write_config({"tool_extras": ["mcp"]})
    monkeypatch.setattr("halia.upgrade.is_installed", lambda _name: True)
    monkeypatch.setattr(
        upgrade, "install_requirements", lambda *a, **k: pytest.fail("reinstalled")
    )

    assert upgrade.repair_extras() == (True, "nothing missing")


def test_repair_extras_reports_failure(monkeypatch: Any) -> None:
    from halia import upgrade
    from halia.config.settings import write_config

    write_config({"tool_extras": ["mcp"]})
    monkeypatch.setattr("halia.upgrade.is_installed", lambda _name: False)
    monkeypatch.setattr(
        upgrade, "install_requirements", lambda *a, **k: (False, "no network")
    )

    assert upgrade.repair_extras() == (False, "no network")
