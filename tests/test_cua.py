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


# ── cua_drag: drawing via mouse drag ──────────────────────────────────────


def test_cua_drag_requires_coordinates(monkeypatch: Any) -> None:
    from halia.skills.cua import CuaDrag

    monkeypatch.setattr("halia.skills.cua._is_cua_enabled", lambda: True)
    out = CuaDrag().run({})
    assert out.startswith("error:")
    assert "required" in out


def test_cua_drag_scales_coordinates(monkeypatch: Any) -> None:
    from halia.skills.cua import CuaDrag, CuaScreenshot

    monkeypatch.setattr("halia.skills.cua._is_cua_enabled", lambda: True)
    calls: dict[str, Any] = {}

    class FakeCua:
        def drag(self, *a: Any, **k: Any) -> str:
            calls["args"] = a
            calls["kwargs"] = k
            return "Dragged"

    monkeypatch.setattr("halia.skills.cua._get_cua", lambda: FakeCua())
    # monkeypatch restores _scale after the test, so no cross-test pollution.
    monkeypatch.setattr(CuaScreenshot, "_scale", 2.0)

    out = CuaDrag().run({
        "from_x": 100, "from_y": 50, "to_x": 300, "to_y": 250,
        "duration_ms": 500, "steps": 20,
    })
    assert out == "Dragged [image 100,50 -> 300,250 -> screen 200,100 -> 600,500]"
    assert calls["args"][:4] == (200.0, 100.0, 600.0, 500.0)
    assert calls["kwargs"] == {"button": "left", "duration_ms": 500, "steps": 20}


# ── cua_window: element positions from the accessibility tree ─────────────


def test_cua_window_requires_pid_and_window_id(monkeypatch: Any) -> None:
    from halia.skills.cua import CuaWindow

    monkeypatch.setattr("halia.skills.cua._is_cua_enabled", lambda: True)
    out = CuaWindow().run({})
    assert out.startswith("error:")
    assert "required" in out


def test_cua_window_formats_elements_and_scales(monkeypatch: Any) -> None:
    from halia.skills.cua import CuaScreenshot, CuaWindow

    monkeypatch.setattr("halia.skills.cua._is_cua_enabled", lambda: True)

    class FakeCua:
        def window_state(
            self, pid: int, window_id: int,
            max_elements: Any = None, max_depth: Any = None,
        ) -> str:
            return (
                '{"element_count": 2, "elements": ['
                '{"element_index": 0, "role": "AXWindow", "label": "Win", '
                '"frame": {"x": 0, "y": 30, "w": 1920, "h": 1050}},'
                '{"element_index": 1, "role": "AXButton", '
                '"frame": {"x": 10, "y": 39, "w": 16, "h": 16}}]}'
            )

    monkeypatch.setattr("halia.skills.cua._get_cua", lambda: FakeCua())
    monkeypatch.setattr(CuaScreenshot, "_scale", 2.0)

    out = CuaWindow().run({"pid": 123, "window_id": 456})
    assert 'AXWindow "Win" -> click (480.0, 277.5)' in out
    assert "AXButton -> click (9.0, 23.5)" in out
    assert "of 2" in out


def test_cua_window_passes_bounds_to_driver(monkeypatch: Any) -> None:
    from halia.skills.cua import CuaWindow

    monkeypatch.setattr("halia.skills.cua._is_cua_enabled", lambda: True)
    calls: dict[str, Any] = {}

    class FakeCua:
        def window_state(
            self, pid: int, window_id: int,
            max_elements: Any = None, max_depth: Any = None,
        ) -> str:
            calls["pid"] = pid
            calls["window_id"] = window_id
            calls["max_elements"] = max_elements
            calls["max_depth"] = max_depth
            return '{"elements": []}'

    monkeypatch.setattr("halia.skills.cua._get_cua", lambda: FakeCua())
    CuaWindow().run({"pid": 12, "window_id": 34, "max_elements": 50, "max_depth": 6})
    assert calls["pid"] == 12
    assert calls["window_id"] == 34
    assert calls["max_elements"] == 50
    assert calls["max_depth"] == 6
