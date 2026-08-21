"""Tests for blended computer backends (browser + CUA routing)."""

from typing import Any


def test_get_computer_backend_defaults_to_auto(monkeypatch: Any) -> None:
    import halia.skills as skills

    monkeypatch.setattr("halia.config.settings.read_config", lambda: {})
    assert skills._get_computer_backend() == "auto"

    # Legacy "halia" maps to browser-only.
    monkeypatch.setattr(
        "halia.config.settings.read_config", lambda: {"computer_backend": "halia"}
    )
    assert skills._get_computer_backend() == "browser"

    monkeypatch.setattr(
        "halia.config.settings.read_config", lambda: {"computer_backend": "cua"}
    )
    assert skills._get_computer_backend() == "cua"

    monkeypatch.setattr(
        "halia.config.settings.read_config", lambda: {"computer_backend": "browser"}
    )
    assert skills._get_computer_backend() == "browser"

    # Unknown value must normalize to "auto" (never silently disable computer tools).
    monkeypatch.setattr(
        "halia.config.settings.read_config", lambda: {"computer_backend": "not-a-real-backend"}
    )
    assert skills._get_computer_backend() == "auto"


def test_cua_is_enabled_allows_blended(monkeypatch: Any) -> None:
    from halia.skills.cua import _is_cua_enabled

    monkeypatch.setattr("halia.config.settings.read_config", lambda: {"computer_backend": "auto"})
    assert _is_cua_enabled() is True

    monkeypatch.setattr("halia.config.settings.read_config", lambda: {"computer_backend": "cua"})
    assert _is_cua_enabled() is True

    monkeypatch.setattr(
        "halia.config.settings.read_config", lambda: {"computer_backend": "browser"}
    )
    assert _is_cua_enabled() is False


def test_browser_launch_kwargs_resolution(monkeypatch: Any) -> None:
    from halia.skills.browser import _browser_launch_kwargs

    # auto + Chrome installed → use system Chrome.
    monkeypatch.setattr("halia.config.settings.read_config", lambda: {})
    monkeypatch.setattr("halia.skills.browser._chrome_installed", lambda: True)
    assert _browser_launch_kwargs() == {"channel": "chrome"}

    # auto + no Chrome → bundled full Chromium (not the headless shell).
    monkeypatch.setattr("halia.skills.browser._chrome_installed", lambda: False)
    assert _browser_launch_kwargs() == {"channel": "chromium"}

    # explicit executable path (Arc, Brave, …) wins over everything.
    monkeypatch.setattr(
        "halia.config.settings.read_config",
        lambda: {"browser_executable_path": "/Applications/Arc.app/Contents/MacOS/Arc"},
    )
    assert _browser_launch_kwargs() == {
        "executable_path": "/Applications/Arc.app/Contents/MacOS/Arc"
    }

    # explicit channel.
    monkeypatch.setattr(
        "halia.config.settings.read_config", lambda: {"browser_channel": "msedge"}
    )
    assert _browser_launch_kwargs() == {"channel": "msedge"}


def test_system_prompt_blended(monkeypatch: Any) -> None:
    from halia.core.agent import _get_system_prompt

    monkeypatch.setattr("halia.skills.available_backends", lambda: {"browser", "cua"})
    prompt = _get_system_prompt()
    assert "two backends are available" in prompt
    assert "EXISTING logged-in session" in prompt


def test_system_prompt_browser_only(monkeypatch: Any) -> None:
    from halia.core.agent import _get_system_prompt

    monkeypatch.setattr("halia.skills.available_backends", lambda: {"browser"})
    assert "BROWSER AUTOMATION" in _get_system_prompt()


def test_system_prompt_cua_only(monkeypatch: Any) -> None:
    from halia.core.agent import _get_system_prompt

    monkeypatch.setattr("halia.skills.available_backends", lambda: {"cua"})
    assert "DESKTOP AUTOMATION" in _get_system_prompt()


def test_system_prompt_no_backends_omits_automation(monkeypatch: Any) -> None:
    from halia.core.agent import _get_system_prompt

    monkeypatch.setattr("halia.skills.available_backends", lambda: set())
    prompt = _get_system_prompt()
    assert "BROWSER AUTOMATION" not in prompt
    assert "DESKTOP AUTOMATION" not in prompt
    assert "two backends are available" not in prompt
