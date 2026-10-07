"""Tests for CUA desktop automation: URL handling and session recovery."""

import base64
import io
import json
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

    class CursorReducedMotion:
        AUTO = "auto"
        ON = "on"
        OFF = "off"

    class CursorThemeSelection:
        def __init__(self, *, theme_id: str, reduced_motion: Any) -> None:
            self.theme_id = theme_id
            self.reduced_motion = reduced_motion

    class SetAgentCursorEnabledInput:
        def __init__(self, *, session: str, enabled: bool) -> None:
            self.session = session
            self.enabled = enabled

    mod.StartSessionInput = StartSessionInput
    mod.GetDesktopStateInput = GetDesktopStateInput
    mod.CursorReducedMotion = CursorReducedMotion
    mod.CursorThemeSelection = CursorThemeSelection
    mod.SetAgentCursorEnabledInput = SetAgentCursorEnabledInput
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
            self.cursor_enabled_calls = 0
            self._first = True

        async def start_session(self, _input: Any) -> None:
            self.start_calls += 1

        async def set_agent_cursor_enabled(self, _input: Any) -> None:
            self.cursor_enabled_calls += 1

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


def test_cua_click_uses_foreground_desktop_delivery(monkeypatch: Any) -> None:
    """Desktop clicks must use InputDeliveryMode.FOREGROUND (driver rejects BACKGROUND)."""
    from enum import Enum

    class InputDeliveryMode(Enum):
        BACKGROUND = "background"
        FOREGROUND = "foreground"

    class ClickButton(Enum):
        LEFT = "left"
        RIGHT = "right"
        MIDDLE = "middle"

    class ActionTarget:
        class DESKTOP:
            def __init__(self, display_id: str) -> None:
                self.display_id = display_id

    class ClickPosition:
        class COORDINATES:
            def __init__(self, x: float, y: float) -> None:
                self.x = x
                self.y = y

    class ClickInput:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    mod = types.ModuleType("cua_driver")
    mod.ActionTarget = ActionTarget
    mod.ClickPosition = ClickPosition
    mod.InputDeliveryMode = InputDeliveryMode
    mod.ClickButton = ClickButton
    mod.ClickInput = ClickInput
    monkeypatch.setitem(sys.modules, "cua_driver", mod)

    captured: dict[str, Any] = {}

    class FakeDriver:
        async def click(self, inp: Any) -> None:
            captured["input"] = inp

    from halia.computer.cua_backend import CuaComputer

    cua = CuaComputer()
    cua._driver = FakeDriver()
    cua._session_started = True

    out = cua.click(100.0, 200.0)

    inp = captured["input"]
    assert inp.kwargs["delivery_mode"] is InputDeliveryMode.FOREGROUND
    assert inp.kwargs["target"].display_id == "primary"
    assert (inp.kwargs["position"].x, inp.kwargs["position"].y) == (100.0, 200.0)
    assert "Clicked left" in out


def test_cua_capture_scope_resolution(monkeypatch: Any) -> None:
    """capture_scope resolves from env/config; unknown/absent → None (driver default)."""
    from enum import Enum

    # cua_driver isn't installed in headless CI — provide a minimal fake module.
    class CaptureScope(Enum):
        AUTO = "auto"
        WINDOW = "window"
        DESKTOP = "desktop"

    mod = types.ModuleType("cua_driver")
    mod.CaptureScope = CaptureScope
    monkeypatch.setitem(sys.modules, "cua_driver", mod)

    from halia.computer.cua_backend import _cua_capture_scope

    monkeypatch.delenv("HALIA_CUA_CAPTURE_SCOPE", raising=False)
    monkeypatch.setattr("halia.config.settings.read_config", lambda: {})
    assert _cua_capture_scope() is None

    monkeypatch.setenv("HALIA_CUA_CAPTURE_SCOPE", "window")
    assert _cua_capture_scope() is CaptureScope.WINDOW

    monkeypatch.setenv("HALIA_CUA_CAPTURE_SCOPE", "desktop")
    assert _cua_capture_scope() is CaptureScope.DESKTOP

    monkeypatch.setenv("HALIA_CUA_CAPTURE_SCOPE", "auto")
    assert _cua_capture_scope() is CaptureScope.AUTO

    monkeypatch.setenv("HALIA_CUA_CAPTURE_SCOPE", "bogus")
    assert _cua_capture_scope() is None


def test_cua_cursor_theme_resolution(monkeypatch: Any) -> None:
    """cursor_theme resolves from env/config; absent → the built-in blue `cua.default`."""
    from enum import Enum

    # cua_driver isn't installed in headless CI — provide a minimal fake module.
    class CursorReducedMotion(Enum):
        AUTO = "auto"
        ON = "on"
        OFF = "off"

    class CursorThemeSelection:
        def __init__(self, *, theme_id: str, reduced_motion: Any) -> None:
            self.theme_id = theme_id
            self.reduced_motion = reduced_motion

    mod = types.ModuleType("cua_driver")
    mod.CursorReducedMotion = CursorReducedMotion
    mod.CursorThemeSelection = CursorThemeSelection
    monkeypatch.setitem(sys.modules, "cua_driver", mod)

    from halia.computer.cua_backend import _cua_cursor_theme

    monkeypatch.delenv("HALIA_CUA_CURSOR_THEME", raising=False)
    monkeypatch.setattr("halia.config.settings.read_config", lambda: {})
    assert _cua_cursor_theme().theme_id == "cua.default"

    monkeypatch.setenv("HALIA_CUA_CURSOR_THEME", "my-cursor")
    theme = _cua_cursor_theme()
    assert theme is not None
    assert theme.theme_id == "my-cursor"


# ── cua_draw_path ──────────────────────────────────────────────────────


def test_cua_draw_path_chains_scaled_drags(monkeypatch: Any) -> None:
    from halia.skills.cua import CuaDrawPath, CuaScreenshot

    monkeypatch.setattr("halia.skills.cua._is_cua_enabled", lambda: True)
    monkeypatch.setattr(CuaScreenshot, "_scale", 2.0)

    calls: list[tuple[float, float, float, float]] = []

    class FakeCua:
        def drag(
            self, fx: float, fy: float, tx: float, ty: float,
            button: str = "left", duration_ms: int | None = None,
            steps: int | None = None,
        ) -> str:
            calls.append((fx, fy, tx, ty))
            return "ok"

    monkeypatch.setattr("halia.skills.cua._get_cua", lambda: FakeCua())

    out = CuaDrawPath().run(
        {"points": [[0, 0], [100, 0], [100, 100]], "smooth": False}
    )
    assert calls == [(0.0, 0.0, 200.0, 0.0), (200.0, 0.0, 200.0, 200.0)]
    assert "2 segments" in out


def test_cua_draw_path_smooth_densifies(monkeypatch: Any) -> None:
    from halia.skills.cua import CuaDrawPath, CuaScreenshot

    monkeypatch.setattr("halia.skills.cua._is_cua_enabled", lambda: True)
    monkeypatch.setattr(CuaScreenshot, "_scale", 1.0)

    count = 0

    class FakeCua:
        def drag(
            self, fx: float, fy: float, tx: float, ty: float,
            button: str = "left", duration_ms: int | None = None,
            steps: int | None = None,
        ) -> str:
            nonlocal count
            count += 1
            return "ok"

    monkeypatch.setattr("halia.skills.cua._get_cua", lambda: FakeCua())
    CuaDrawPath().run({"points": [[0, 0], [50, 0], [100, 0]], "smooth": True})
    assert count > 2  # smoothing adds intermediate segments


def test_cua_draw_path_requires_points(monkeypatch: Any) -> None:
    from halia.skills.cua import CuaDrawPath

    monkeypatch.setattr("halia.skills.cua._is_cua_enabled", lambda: True)
    out = CuaDrawPath().run({"points": [[0, 0]]})
    assert out.startswith("error:")


def test_cua_fill_spans_rasterizes_square() -> None:
    """A square rasterizes into horizontal scanlines clipped to its interior."""
    from halia.skills.cua import _fill_spans

    square = [(0, 0), (10, 0), (10, 10), (0, 10)]
    spans = _fill_spans(square, spacing=5.0, direction="horizontal")
    # Scanlines at y=0 and y=5 (y=10 is the top boundary, excluded half-open).
    assert spans == [((0.0, 0.0), (10.0, 0.0)), ((0.0, 5.0), (10.0, 5.0))]

    vertical = _fill_spans(square, spacing=5.0, direction="vertical")
    assert vertical == [((0.0, 0.0), (0.0, 10.0)), ((5.0, 0.0), (5.0, 10.0))]


def test_cua_fill_path_drags_inside_shape(monkeypatch: Any) -> None:
    from halia.skills.cua import CuaFillPath, CuaScreenshot

    monkeypatch.setattr("halia.skills.cua._is_cua_enabled", lambda: True)
    monkeypatch.setattr(CuaScreenshot, "_scale", 1.0)

    calls: list[tuple[float, float, float, float]] = []

    class FakeCua:
        def drag(
            self, fx: float, fy: float, tx: float, ty: float,
            button: str = "left", duration_ms: int | None = None,
            steps: int | None = None,
        ) -> str:
            calls.append((fx, fy, tx, ty))
            return "ok"

    monkeypatch.setattr("halia.skills.cua._get_cua", lambda: FakeCua())

    out = CuaFillPath().run(
        {"points": [[0, 0], [10, 0], [10, 10], [0, 10]], "spacing": 5}
    )
    assert calls == [(0.0, 0.0, 10.0, 0.0), (0.0, 5.0, 10.0, 5.0)]
    assert "2 horizontal strokes" in out


def test_cua_fill_path_rejects_open_or_small_outline(monkeypatch: Any) -> None:
    from halia.skills.cua import CuaFillPath

    monkeypatch.setattr("halia.skills.cua._is_cua_enabled", lambda: True)
    assert CuaFillPath().run({"points": [[0, 0], [5, 5]]}).startswith("error:")


def test_cua_undo_uses_platform_hotkey(monkeypatch: Any) -> None:
    from halia.skills.cua import CuaUndo

    monkeypatch.setattr("halia.skills.cua._is_cua_enabled", lambda: True)
    monkeypatch.setattr(sys, "platform", "darwin")

    pressed: list[list[str]] = []

    class FakeCua:
        def hotkey(self, keys: list[str]) -> str:
            pressed.append(keys)
            return "ok"

    monkeypatch.setattr("halia.skills.cua._get_cua", lambda: FakeCua())
    out = CuaUndo().run({"times": 2})
    assert pressed == [["cmd", "z"], ["cmd", "z"]]
    assert "cmd+z" in out


# ── CUA system prompt guidance ────────────────────────────────────────────


def test_cua_prompt_scopes_cua_open_url_to_web_only(monkeypatch: Any) -> None:
    from halia.core.agent import _get_system_prompt

    monkeypatch.setattr("halia.skills.available_backends", lambda: {"cua"})
    prompt = _get_system_prompt()
    assert "cua_open_url ONLY for http/https" in prompt
    assert "NEVER use it for local files" in prompt


# ── cua_screenshot: fixed coordinate space ────────────────────────────────


def test_cua_screenshot_detail_controls_resolution_and_scale(
    monkeypatch: Any, tmp_path: Any,
) -> None:
    """'high' = 1600px, 'low' = 1024px; _scale maps image→screen correctly either way."""
    from halia.skills.cua import CuaScreenshot

    monkeypatch.setattr("halia.skills.cua._is_cua_enabled", lambda: True)
    monkeypatch.setattr("halia.skills.cua._get_screenshots_dir", lambda: tmp_path / "shots")

    img_path = tmp_path / "screen.png"
    Image.new("RGB", (3024, 1964), (255, 255, 255)).save(img_path)

    class FakeCua:
        def screenshot(self, path: str | None = None) -> str:
            return str(img_path)

    monkeypatch.setattr("halia.skills.cua._get_cua", lambda: FakeCua())

    def width_of(b64: str) -> int:
        buf = io.BytesIO(base64.b64decode(b64))
        return Image.open(buf).size[0]

    try:
        CuaScreenshot().run({"detail": "high"})
        assert width_of(CuaScreenshot._pending_image) == 1600
        assert CuaScreenshot._scale == 3024 / 1600

        CuaScreenshot().run({"detail": "low"})
        assert width_of(CuaScreenshot._pending_image) == 1024
        assert CuaScreenshot._scale == 3024 / 1024

        # Coordinates map image→screen via the LAST screenshot's scale either way:
        # a click at x=512 on the low-res image lands at real x=1512.
        assert 512 * CuaScreenshot._scale == 1512.0
    finally:
        CuaScreenshot._scale = 1.0
        CuaScreenshot._pending_image = None
        CuaScreenshot._pending_detail = None


def test_cua_screenshot_persists_to_screenshots_dir(monkeypatch: Any, tmp_path: Any) -> None:
    """The captured screenshot is saved to the screenshots dir and reported back."""
    from halia.skills.cua import CuaScreenshot

    monkeypatch.setattr("halia.skills.cua._is_cua_enabled", lambda: True)
    shots = tmp_path / "shots"
    monkeypatch.setattr("halia.skills.cua._get_screenshots_dir", lambda: shots)

    img_path = tmp_path / "screen.png"
    Image.new("RGB", (1600, 900), (255, 0, 0)).save(img_path)

    class FakeCua:
        def screenshot(self, path: str | None = None) -> str:
            dest = Path(path)
            dest.write_bytes(img_path.read_bytes())
            return str(dest)

    monkeypatch.setattr("halia.skills.cua._get_cua", lambda: FakeCua())

    out = CuaScreenshot().run({"grid": False})
    files = list(shots.iterdir())
    assert len(files) == 1
    assert files[0].suffix == ".png"
    assert "Saved to" in out


def test_cua_screenshot_prunes_old_files(monkeypatch: Any, tmp_path: Any) -> None:
    """Only the most recent `screenshot_keep` screenshots are retained."""
    from halia.skills.cua import _prune_screenshots

    shots = tmp_path / "shots"
    shots.mkdir()

    import os

    for i in range(5):
        p = shots / f"shot{i}.png"
        p.write_bytes(b"x")
        os.utime(p, (i, i))  # increasing mtimes: shot0 oldest, shot4 newest

    monkeypatch.setattr("halia.skills.cua._get_screenshots_dir", lambda: shots)
    monkeypatch.setattr("halia.skills.cua._screenshot_keep", lambda: 2)

    _prune_screenshots()

    remaining = sorted(p.name for p in shots.iterdir())
    assert remaining == ["shot3.png", "shot4.png"]


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


def test_cua_window_keeps_frames_in_the_window_s_own_space(monkeypatch: Any) -> None:
    """Window element frames must not be rescaled by the desktop screenshot's scale."""
    from halia.skills.cua import CuaScreenshot, CuaWindow

    monkeypatch.setattr("halia.skills.cua._is_cua_enabled", lambda: True)

    class FakeCua:
        def window_state(
            self, pid: int, window_id: int,
            max_elements: Any = None, max_depth: Any = None,
            screenshot_out_file: Any = None,
        ) -> str:
            return (
                '{"window_id": 68, "window_title": "Doc", "pid": 662,'
                ' "window_bounds": {"x": 0, "y": 30, "width": 1920, "height": 1050},'
                ' "element_count": 2, "elements": ['
                '{"element_index": 0, "role": "AXWindow", "label": "Win",'
                ' "frame": {"x": 0, "y": 30, "w": 1920, "h": 1050},'
                ' "element_token": "tok-0"},'
                '{"element_index": 1, "role": "AXButton",'
                ' "frame": {"x": 10, "y": 39, "w": 16, "h": 16}}]}'
            )

    monkeypatch.setattr("halia.skills.cua._get_cua", lambda: FakeCua())
    # A desktop screenshot with a non-1.0 scale must not leak into window frames.
    monkeypatch.setattr(CuaScreenshot, "_scale", 2.0)

    out = CuaWindow().run({"pid": 662, "window_id": 68})
    assert '[0] AXWindow "Win" (0,30 1920x1050) token=tok-0' in out
    assert "[1] AXButton (10,39 16x16)" in out
    assert 'window 68 "Doc" (pid 662) 1920x1050 at (0,30)' in out
    assert "of 2" in out


def test_cua_window_surfaces_a_degraded_tree(monkeypatch: Any) -> None:
    """An empty degraded tree must say why, not read as 'no controls here'."""
    from halia.skills.cua import CuaWindow

    monkeypatch.setattr("halia.skills.cua._is_cua_enabled", lambda: True)

    class FakeCua:
        def window_state(
            self, pid: int, window_id: int,
            max_elements: Any = None, max_depth: Any = None,
            screenshot_out_file: Any = None,
        ) -> str:
            return (
                '{"window_id": 68, "degraded": true,'
                ' "degraded_reason": "ax_window_unresolved: no AXWindow matches",'
                ' "element_count": 0, "elements": []}'
            )

    monkeypatch.setattr("halia.skills.cua._get_cua", lambda: FakeCua())
    out = CuaWindow().run({"pid": 662, "window_id": 68})
    assert "degraded" in out
    assert "ax_window_unresolved" in out


def test_cua_window_passes_bounds_to_driver(monkeypatch: Any) -> None:
    from halia.skills.cua import CuaWindow

    monkeypatch.setattr("halia.skills.cua._is_cua_enabled", lambda: True)
    calls: dict[str, Any] = {}

    class FakeCua:
        def window_state(
            self, pid: int, window_id: int,
            max_elements: Any = None, max_depth: Any = None,
            screenshot_out_file: Any = None,
        ) -> str:
            calls["pid"] = pid
            calls["window_id"] = window_id
            calls["max_elements"] = max_elements
            calls["max_depth"] = max_depth
            calls["screenshot_out_file"] = screenshot_out_file
            return '{"elements": []}'

    monkeypatch.setattr("halia.skills.cua._get_cua", lambda: FakeCua())
    CuaWindow().run({"pid": 12, "window_id": 34, "max_elements": 50, "max_depth": 6})
    assert calls["pid"] == 12
    assert calls["window_id"] == 34
    assert calls["max_elements"] == 50
    assert calls["max_depth"] == 6
    # No screenshot requested: the element tree is the point, and the inline PNG
    # would be ~1 MB of base64.
    assert calls["screenshot_out_file"] is None


def test_cua_window_screenshot_writes_and_stages_the_window_image(
    monkeypatch: Any, tmp_path: Any
) -> None:
    """screenshot=true captures the window itself, for windows no desktop grab can show."""
    from halia.skills.cua import CuaWindow

    monkeypatch.setattr("halia.skills.cua._is_cua_enabled", lambda: True)
    dest = tmp_path / "window.png"

    class FakeCua:
        def window_state(
            self, pid: int, window_id: int,
            max_elements: Any = None, max_depth: Any = None,
            screenshot_out_file: Any = None,
        ) -> str:
            assert screenshot_out_file == str(dest)
            Image.new("RGB", (40, 20), "white").save(screenshot_out_file)
            return '{"window_id": 7, "elements": []}'

    monkeypatch.setattr("halia.skills.cua._get_cua", lambda: FakeCua())
    monkeypatch.setattr("halia.skills.cua._screenshot_dest", lambda: dest)
    monkeypatch.setattr("halia.skills.cua._prune_screenshots", lambda: None)
    CuaWindow._pending_image = None

    out = CuaWindow().run({"pid": 1, "window_id": 7, "screenshot": True})
    assert "Window screenshot attached (40x20)" in out
    assert CuaWindow._pending_image
    CuaWindow._pending_image = None


def test_cua_window_stale_id_error_hints_to_rerun_desktop(monkeypatch: Any) -> None:
    from halia.skills.cua import CuaWindow

    monkeypatch.setattr("halia.skills.cua._is_cua_enabled", lambda: True)

    class FakeCua:
        def window_state(
            self, pid: int, window_id: int,
            max_elements: Any = None, max_depth: Any = None,
        ) -> str:
            raise RuntimeError(
                "window_id 97809 is not a live window (closed, or the id is stale)"
            )

    monkeypatch.setattr("halia.skills.cua._get_cua", lambda: FakeCua())
    out = CuaWindow().run({"pid": 97809, "window_id": 97809})
    assert out.startswith("error:")
    assert "cua_desktop" in out


# ── cua_desktop: compact accessibility-tree summary ──────────────────────


def test_summarize_apps_keeps_app_names_and_pids() -> None:
    from halia.skills.cua import _summarize_apps

    tree = json.dumps({
        "apps": [
            {"name": "Slack", "pid": 2104,
             "windows": [{"window_id": 1, "title": "Slack"}]},
            {"name": "Arc", "pid": 999,
             "windows": [{"window_id": 2, "title": "AutoDraw"}]},
        ]
    })
    out = _summarize_apps(tree)
    assert "Slack" in out
    assert "Arc" in out
    assert "pid 2104" in out
    # Window ids come from list_windows instead: this tree only lists on-screen
    # windows and is not a reliable source of a real window-server id.
    assert "win 2" not in out


def test_summarize_apps_truncates_unparseable_tree() -> None:
    from halia.skills.cua import _summarize_apps

    raw = "x" * 5000
    out = _summarize_apps(raw)
    assert len(out) <= 2500
    assert out.endswith("…")


def test_summarize_apps_orders_frontmost_first() -> None:
    from halia.skills.cua import _summarize_apps

    tree = json.dumps({
        "apps": [
            {"name": "Slack", "pid": 1, "windows": []},
            {"name": "Arc", "pid": 2, "frontmost": True,
             "windows": [{"window_id": 9, "title": "Canva"}]},
        ]
    })
    out = _summarize_apps(tree)
    assert out.index("Arc") < out.index("Slack")


def test_summarize_apps_notes_omitted_apps() -> None:
    from halia.skills.cua import _summarize_apps

    tree = json.dumps({
        "apps": [
            {"name": "Alpha", "pid": 1, "windows": []},
            {"name": "Beta", "pid": 2, "windows": []},
            {"name": "Gamma", "pid": 3, "windows": []},
        ]
    })
    out = _summarize_apps(tree, max_chars=40)
    assert "omitted" in out


# ── cua_desktop: the window list ─────────────────────────────────────────


def test_summarize_windows_lists_off_screen_windows_with_real_ids() -> None:
    from halia.skills.cua import _summarize_windows

    raw = json.dumps({
        "current_space_id": 3,
        "windows": [
            {"window_id": 68, "pid": 662, "app_name": "Arc", "title": "News",
             "bounds": {"x": 0, "y": 30, "width": 1920, "height": 1050},
             "is_on_screen": True, "z_index": 44},
            {"window_id": 85, "pid": 662, "app_name": "Arc", "title": "",
             "bounds": {"x": 0, "y": 0, "width": 1920, "height": 30},
             "is_on_screen": False, "z_index": 56},
        ],
    })
    out = _summarize_windows(raw)
    assert "2 total, 1 on screen" in out
    assert 'Arc (pid 662): [68] "News" 1920x1050 on-screen' in out
    assert "[85]" in out and "off-screen" in out


def test_summarize_windows_puts_on_screen_windows_first() -> None:
    from halia.skills.cua import _summarize_windows

    raw = json.dumps({
        "windows": [
            {"window_id": 2, "pid": 1, "app_name": "Beta", "title": "",
             "is_on_screen": False, "z_index": 5},
            {"window_id": 1, "pid": 2, "app_name": "Alpha", "title": "",
             "is_on_screen": True, "z_index": 1},
        ],
    })
    out = _summarize_windows(raw)
    assert out.index("Alpha") < out.index("Beta")


def test_summarize_windows_falls_back_to_raw_text() -> None:
    from halia.skills.cua import _summarize_windows

    assert _summarize_windows("not json") == "not json"


def test_cua_desktop_includes_the_window_list(monkeypatch: Any) -> None:
    """cua_desktop must source window ids from list_windows, not the AX tree."""
    from halia.skills.cua import CuaDesktopState

    monkeypatch.setattr("halia.skills.cua._is_cua_enabled", lambda: True)

    class FakeCua:
        def desktop_state(self) -> str:
            return "Desktop state:"

        def list_windows(self) -> str:
            return json.dumps({
                "windows": [
                    {"window_id": 877, "pid": 47749, "app_name": "Code",
                     "title": "halia", "bounds": {"width": 1920, "height": 1050},
                     "is_on_screen": False, "z_index": 3},
                ],
            })

        def accessibility_tree(self) -> str:
            return json.dumps({"apps": [{"name": "Code", "pid": 47749}]})

    monkeypatch.setattr("halia.skills.cua._get_cua", lambda: FakeCua())
    out = CuaDesktopState().run({})
    assert 'Code (pid 47749): [877] "halia" 1920x1050 off-screen' in out
    assert "running apps (1)" in out


def test_security_dialog_hint_detects_system_prompts() -> None:
    from halia.skills.cua import _security_dialog_hint

    security_agent = json.dumps({
        "apps": [
            {"name": "SecurityAgent", "bundle_id": "com.apple.SecurityAgent", "windows": []},
            {"name": "Slack", "bundle_id": "com.tinyspeck.slackmacgap", "windows": []},
        ]
    })
    assert "SECURITY DIALOG" in (_security_dialog_hint(security_agent) or "")

    benign = json.dumps({
        "apps": [
            {"name": "Slack", "bundle_id": "com.tinyspeck.slackmacgap",
             "windows": [{"window_id": 1, "title": "Slack"}]},
        ]
    })
    assert _security_dialog_hint(benign) is None
    assert _security_dialog_hint("not json") is None


# ── window-scoped actions ─────────────────────────────────────────────────


class _FakeToolResult:
    """Minimal stand-in for the driver's ToolResult."""

    def __init__(self, structured: str = '{"ok": true}', text: str = "") -> None:
        self.structured_json = structured
        self.text = text


class _FakeToolDriver:
    """Records what goes through the driver's generic tool channel."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments_json: str) -> _FakeToolResult:
        self.calls.append((name, json.loads(arguments_json)))
        return _FakeToolResult()


def _backend_with(driver: Any) -> Any:
    """A CuaComputer wired to a fake driver, bypassing the embedded host launch."""
    from halia.computer.cua_backend import CuaComputer

    cua = CuaComputer()
    cua._driver = driver
    cua._session_started = True
    return cua


def test_cua_click_window_target_uses_the_tool_channel() -> None:
    """A window click must address pid/window_id + token, never the desktop target."""
    driver = _FakeToolDriver()
    cua = _backend_with(driver)

    out = cua.click(pid=662, window_id=68, element_token="tok-1")

    assert [name for name, _ in driver.calls] == ["click"]
    payload = driver.calls[0][1]
    assert payload["pid"] == 662
    assert payload["window_id"] == 68
    assert payload["element_token"] == "tok-1"
    assert payload["session"] == cua._session_name
    assert "window 68" in out


def test_cua_click_window_target_accepts_element_index_and_snapshot() -> None:
    driver = _FakeToolDriver()
    cua = _backend_with(driver)

    cua.click(pid=1, window_id=2, element_index=7, snapshot_id="sabc1234")

    payload = driver.calls[0][1]
    assert payload["element_index"] == 7
    assert payload["snapshot_id"] == "sabc1234"
    assert "element_token" not in payload


def test_cua_click_window_target_accepts_coordinates() -> None:
    driver = _FakeToolDriver()
    cua = _backend_with(driver)

    cua.click(100, 250, pid=1, window_id=2)

    name, payload = driver.calls[0]
    assert name == "click"
    assert payload["x"] == 100
    assert payload["y"] == 250
    # No scope override: the driver's own default already means window-local pixels.
    assert "scope" not in payload


def test_cua_click_window_target_without_any_address_errors() -> None:
    driver = _FakeToolDriver()
    cua = _backend_with(driver)

    out = cua.click(pid=1, window_id=2)

    assert out.startswith("error:")
    assert not driver.calls


def test_cua_type_window_target_carries_the_element() -> None:
    driver = _FakeToolDriver()
    cua = _backend_with(driver)

    out = cua.type_text("hello", pid=662, window_id=68, element_index=3, snapshot_id="s1")

    name, payload = driver.calls[0]
    assert name == "type_text"
    assert payload["text"] == "hello"
    assert payload["element_index"] == 3
    assert payload["window_id"] == 68
    assert "window 68" in out


def test_cua_scroll_window_target_rolls_at_the_point() -> None:
    driver = _FakeToolDriver()
    cua = _backend_with(driver)

    cua.scroll(10, 20, "down", 5, pid=1, window_id=2)

    name, payload = driver.calls[0]
    assert name == "scroll"
    assert (payload["x"], payload["y"]) == (10, 20)
    assert payload["direction"] == "down"
    assert payload["amount"] == 5


def test_cua_drag_window_target_passes_coordinates_unchanged() -> None:
    driver = _FakeToolDriver()
    cua = _backend_with(driver)

    cua.drag(1, 2, 3, 4, pid=9, window_id=8)

    name, payload = driver.calls[0]
    assert name == "drag"
    assert (payload["from_x"], payload["from_y"]) == (1, 2)
    assert (payload["to_x"], payload["to_y"]) == (3, 4)
    assert payload["window_id"] == 8


def test_cua_window_state_skips_the_inline_screenshot() -> None:
    """The element tree is the point; the inline PNG is ~1 MB of wasted base64."""
    driver = _FakeToolDriver()
    cua = _backend_with(driver)

    cua.window_state(662, 68)

    name, payload = driver.calls[0]
    assert name == "get_window_state"
    assert payload["include_screenshot"] is False
    assert "screenshot_out_file" not in payload


def test_cua_window_state_asks_for_a_file_when_given_one() -> None:
    driver = _FakeToolDriver()
    cua = _backend_with(driver)

    cua.window_state(662, 68, screenshot_out_file="/tmp/win.png")

    payload = driver.calls[0][1]
    assert payload["screenshot_out_file"] == "/tmp/win.png"
    assert payload["include_screenshot"] is False


def test_cua_list_windows_uses_the_tool_channel() -> None:
    driver = _FakeToolDriver()
    cua = _backend_with(driver)

    cua.list_windows()

    name, payload = driver.calls[0]
    assert name == "list_windows"
    # No pid filter and no on_screen_only: every window, including off-screen ones.
    assert "pid" not in payload
    assert "on_screen_only" not in payload


def test_cua_click_forwards_a_window_target_from_skill_args(monkeypatch: Any) -> None:
    from halia.skills.cua import CuaClick

    monkeypatch.setattr("halia.skills.cua._is_cua_enabled", lambda: True)
    seen: dict[str, Any] = {}

    class FakeCua:
        def click(self, x: Any, y: Any, button: str, **kwargs: Any) -> str:
            seen.update(x=x, y=y, button=button, **kwargs)
            return "Clicked"

    monkeypatch.setattr("halia.skills.cua._get_cua", lambda: FakeCua())
    out = CuaClick().run({"pid": 662, "window_id": 68, "element_token": "tok-9"})

    assert seen["pid"] == 662
    assert seen["window_id"] == 68
    assert seen["element_token"] == "tok-9"
    assert seen["x"] is None and seen["y"] is None
    assert out == "Clicked"


def test_cua_click_ignores_window_fields_without_both_ids(monkeypatch: Any) -> None:
    """A lone pid must not silently drop the coordinate scaling path."""
    from halia.skills.cua import CuaClick, CuaScreenshot

    monkeypatch.setattr("halia.skills.cua._is_cua_enabled", lambda: True)
    seen: dict[str, Any] = {}

    class FakeCua:
        def click(self, x: Any, y: Any, button: str, **kwargs: Any) -> str:
            seen.update(x=x, y=y, **kwargs)
            return "Clicked"

    monkeypatch.setattr("halia.skills.cua._get_cua", lambda: FakeCua())
    monkeypatch.setattr(CuaScreenshot, "_scale", 2.0)

    CuaClick().run({"x": 10, "y": 20, "pid": 662})
    assert seen["x"] == 20.0 and seen["y"] == 40.0


def test_cua_type_window_target_rejects_clear(monkeypatch: Any) -> None:
    """clear=true cannot target a window, so it must refuse rather than clear the wrong field."""
    from halia.skills.cua import CuaType

    monkeypatch.setattr("halia.skills.cua._is_cua_enabled", lambda: True)
    called: list[Any] = []

    class FakeCua:
        def type_text(self, *a: Any, **k: Any) -> str:
            called.append((a, k))
            return "Typed"

    monkeypatch.setattr("halia.skills.cua._get_cua", lambda: FakeCua())
    out = CuaType().run({"text": "hi", "clear": True, "pid": 1, "window_id": 2})

    assert out.startswith("error:")
    assert not called


def test_cua_scroll_window_target_needs_a_token_or_coordinates(monkeypatch: Any) -> None:
    from halia.skills.cua import CuaScroll

    monkeypatch.setattr("halia.skills.cua._is_cua_enabled", lambda: True)
    called: list[Any] = []

    class FakeCua:
        def scroll(self, *a: Any, **k: Any) -> str:
            called.append((a, k))
            return "Scrolled"

    monkeypatch.setattr("halia.skills.cua._get_cua", lambda: FakeCua())
    out = CuaScroll().run({"pid": 1, "window_id": 2, "direction": "down"})

    assert out.startswith("error:")
    assert not called


def test_window_target_args_coerces_strings_and_names_bad_input() -> None:
    from halia.skills.cua import _window_target_args

    # Models routinely stringify numbers; that must still address the window.
    assert _window_target_args({"pid": "662", "window_id": "68"}) == {
        "pid": 662,
        "window_id": 68,
    }
    # A lone id cannot address a window, so the desktop path is used instead.
    assert _window_target_args({"pid": 662}) == {}
    assert _window_target_args({"window_id": 68}) == {}
    # Garbage is reported against the field, not as a bare int() traceback.
    try:
        _window_target_args({"pid": 662, "window_id": "the window"})
    except ValueError as exc:
        assert "window_id" in str(exc)
    else:
        raise AssertionError("expected a ValueError naming window_id")


def test_cua_click_reports_a_bad_window_id_as_an_error(monkeypatch: Any) -> None:
    from halia.skills.cua import CuaClick

    monkeypatch.setattr("halia.skills.cua._is_cua_enabled", lambda: True)

    class FakeCua:
        def click(self, *a: Any, **k: Any) -> str:  # pragma: no cover - must not run
            raise AssertionError("click must not be reached with a bad window_id")

    monkeypatch.setattr("halia.skills.cua._get_cua", lambda: FakeCua())
    out = CuaClick().run({"pid": 1, "window_id": "oops", "element_token": "t"})
    assert out.startswith("error:")
    assert "window_id" in out
