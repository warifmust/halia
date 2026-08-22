"""Tests for CUA desktop automation: URL handling and session recovery."""

import base64
import io
import sys
import types
from pathlib import Path
from typing import Any

from PIL import Image

# ── cua_open_url: only web URLs, never local files/folders ────────────────


def test_cua_open_url_rejects_file_url(monkeypatch: Any) -> None:
    from halia.skills.cua import CuaOpenUrl

    monkeypatch.setattr("halia.skills.cua._is_cua_enabled", lambda: True)
    out = CuaOpenUrl().run({"url": "file:///Users/arif.mustaffa/Desktop/Files"})
    assert out.startswith("error:")
    assert "web URLs" in out
    assert "cua_double_click" in out


def test_cua_open_url_rejects_local_paths(monkeypatch: Any) -> None:
    from halia.skills.cua import CuaOpenUrl

    monkeypatch.setattr("halia.skills.cua._is_cua_enabled", lambda: True)
    for url in ("/Users/arif.mustaffa/Desktop/Files", "~/Desktop/Files", "./files"):
        out = CuaOpenUrl().run({"url": url})
        assert out.startswith("error:"), url
        assert "local path" in out, url


def test_cua_open_url_prepends_https_for_bare_domain(monkeypatch: Any) -> None:
    from halia.skills.cua import CuaOpenUrl

    monkeypatch.setattr("halia.skills.cua._is_cua_enabled", lambda: True)
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/xdg-open")
    monkeypatch.setattr("time.sleep", lambda _s: None)

    calls: list[list[str]] = []

    class _FakePopen:
        def __init__(self, cmd: list[str], *a: Any, **k: Any) -> None:
            calls.append(cmd)

    monkeypatch.setattr("subprocess.Popen", _FakePopen)

    out = CuaOpenUrl().run({"url": "google.com"})
    assert out.startswith("Opened ")
    assert calls == [["xdg-open", "https://google.com"]]


# ── CUA session recovery ──────────────────────────────────────────────────


def test_cua_session_restarts_after_session_ended(monkeypatch: Any) -> None:
    """A dead cua-driver session is restarted once instead of failing forever."""

    mod: Any = types.ModuleType("cua_driver")

    class StartSessionInput:
        def __init__(
            self, session: str = "", capture_scope: Any = None, cursor_theme: Any = None
        ) -> None:
            pass

    class GetDesktopStateInput:
        def __init__(self, session: str = "", screenshot_out_file: Any = None) -> None:
            pass

    mod.StartSessionInput = StartSessionInput
    mod.GetDesktopStateInput = GetDesktopStateInput
    monkeypatch.setitem(sys.modules, "cua_driver", mod)

    # A real (tiny) PNG so the screenshot pipeline writes a valid image file.
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (255, 0, 0)).save(buf, format="PNG")
    png_b64 = base64.b64encode(buf.getvalue()).decode()

    class FakeImage:
        def __init__(self) -> None:
            self.data_base64 = png_b64
            self.data = None
            self.url = None

    class FakeDesktop:
        def __init__(self, is_error: bool = False, text: str = "", images: Any = None) -> None:
            self.is_error = is_error
            self.text = text
            self.error_code = ""
            self.images = images or []

    class FakeDriver:
        def __init__(self) -> None:
            self.start_calls = 0
            self.state_calls = 0
            self._first = True

        async def start_session(self, _input: Any) -> None:
            self.start_calls += 1

        async def get_desktop_state(self, _input: Any) -> FakeDesktop:
            self.state_calls += 1
            if self._first:
                self._first = False
                return FakeDesktop(
                    is_error=True,
                    text="this session has ended; call start_session explicitly to reuse its label",
                )
            return FakeDesktop(images=[FakeImage()])

        async def end_session(self, _input: Any) -> None:
            pass

        async def shutdown(self) -> None:
            pass

    from halia.computer.cua_backend import CuaComputer

    cua = CuaComputer()
    driver = FakeDriver()
    cua._driver = driver
    cua._session_started = True  # simulate a stale, already-ended session

    path = cua.screenshot()

    assert driver.state_calls == 2  # first call failed, second succeeded
    assert driver.start_calls == 1  # restarted exactly once
    assert Path(path).exists()


# ── CUA system prompt guidance ────────────────────────────────────────────


def test_cua_prompt_scopes_cua_open_url_to_web_only(monkeypatch: Any) -> None:
    from halia.core.agent import _get_system_prompt

    monkeypatch.setattr("halia.skills.available_backends", lambda: {"cua"})
    prompt = _get_system_prompt()
    assert "cua_open_url ONLY for http/https" in prompt
    assert "NEVER use it for local files" in prompt
