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
    """cursor_theme resolves from env/config; absent → None (driver default)."""
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
    assert _cua_cursor_theme() is None

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


def test_cua_screenshot_detail_does_not_change_coordinate_space(
    monkeypatch: Any, tmp_path: Any,
) -> None:
    """high vs low detail produce the SAME width, so click coordinates stay valid."""
    from halia.skills.cua import CuaScreenshot

    monkeypatch.setattr("halia.skills.cua._is_cua_enabled", lambda: True)
    monkeypatch.setattr("halia.skills.cua._get_screenshots_dir", lambda: tmp_path / "shots")

    img_path = tmp_path / "screen.png"
    Image.new("RGB", (3024, 1964), (255, 255, 255)).save(img_path)

    class FakeCua:
        def screenshot(self, path: str | None = None) -> str:
            return str(img_path)

    monkeypatch.setattr("halia.skills.cua._get_cua", lambda: FakeCua())

    try:
        CuaScreenshot().run({"detail": "high"})
        high_scale = CuaScreenshot._scale
        high_img = CuaScreenshot._pending_image

        CuaScreenshot().run({"detail": "low"})
        low_scale = CuaScreenshot._scale
        low_img = CuaScreenshot._pending_image

        # Same coordinate space regardless of detail — this is the invariant that
        # keeps cua_click/cua_drag/cua_scroll targets valid between screenshots.
        assert high_scale == low_scale
        assert high_scale == 3024 / 1600  # resized from 3024px to the fixed 1600px

        def width_of(b64: str) -> int:
            buf = io.BytesIO(base64.b64decode(b64))
            return Image.open(buf).size[0]

        assert width_of(high_img) == 1600
        assert width_of(low_img) == 1600
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


def test_summarize_desktop_tree_keeps_app_names_and_window_ids() -> None:
    from halia.skills.cua import _summarize_desktop_tree

    tree = json.dumps({
        "apps": [
            {"name": "Slack", "pid": 2104,
             "windows": [{"window_id": 1, "title": "Slack"}]},
            {"name": "Arc", "pid": 999,
             "windows": [{"window_id": 2, "title": "AutoDraw"}]},
        ]
    })
    out = _summarize_desktop_tree(tree)
    assert "Slack" in out
    assert "Arc" in out
    assert "pid 2104" in out
    assert "win 2 AutoDraw" in out


def test_summarize_desktop_tree_truncates_unparseable_tree() -> None:
    from halia.skills.cua import _summarize_desktop_tree

    raw = "x" * 5000
    out = _summarize_desktop_tree(raw)
    assert len(out) <= 2500
    assert out.endswith("…")


def test_summarize_desktop_tree_orders_frontmost_first() -> None:
    from halia.skills.cua import _summarize_desktop_tree

    tree = json.dumps({
        "apps": [
            {"name": "Slack", "pid": 1, "windows": []},
            {"name": "Arc", "pid": 2, "frontmost": True,
             "windows": [{"window_id": 9, "title": "Canva"}]},
        ]
    })
    out = _summarize_desktop_tree(tree)
    assert out.index("Arc") < out.index("Slack")


def test_summarize_desktop_tree_notes_omitted_apps() -> None:
    from halia.skills.cua import _summarize_desktop_tree

    tree = json.dumps({
        "apps": [
            {"name": "Alpha", "pid": 1, "windows": []},
            {"name": "Beta", "pid": 2, "windows": []},
            {"name": "Gamma", "pid": 3, "windows": []},
        ]
    })
    out = _summarize_desktop_tree(tree, max_chars=40)
    assert "omitted" in out


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
