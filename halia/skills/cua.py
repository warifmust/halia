"""CUA (Computer Use Agent) skills — desktop automation via cua-driver.

Provides desktop-level automation skills that use the cua-driver SDK.
Available in blended mode (computer_backend "auto") or forced ("cua"),
provided a graphical display exists.

Unlike browser skills (Playwright), CUA skills can:
- Control any desktop application (not just browser)
- Work in background without stealing focus
- Interact with native OS elements

Trust note: CUA operations are logged for audit but bypass halia's
filesystem guards (not applicable to desktop UI operations).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from halia.skills.base import Skill


def _is_cua_enabled() -> bool:
    """Check if the CUA backend is enabled (blended "auto" or forced "cua")."""
    from halia.config.settings import read_config
    config = read_config()
    return config.get("computer_backend", "auto") in ("auto", "cua")


def _get_cua() -> Any:
    """Get the CUA computer instance."""
    from halia.computer.cua_backend import cua_available, get_cua_computer
    if not cua_available():
        raise RuntimeError(
            "CUA desktop automation requires a graphical desktop "
            "(X11/Wayland on Linux, or a logged-in macOS/Windows session). "
            "This environment looks headless — use browser automation or "
            "HTTP requests instead."
        )
    return get_cua_computer()


def _overlay_grid(img: Any, step: int = 100) -> Any:
    """Draw a faint coordinate grid + axis labels for precise click targeting."""
    from PIL import Image, ImageDraw, ImageFont

    overlay = Image.new("RGBA", img.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)
    width, height = img.size
    grid_color = (0, 0, 0, 18)
    label_color = (0, 0, 0, 64)

    for x in range(step, width, step):
        draw.line([(x, 0), (x, height)], fill=grid_color, width=1)
    for y in range(step, height, step):
        draw.line([(0, y), (width, y)], fill=grid_color, width=1)

    try:
        font = ImageFont.load_default(size=10)
    except TypeError:  # Pillow < 10 lacks the size argument
        font = ImageFont.load_default()

    for x in range(0, width, step):
        draw.text((x + 2, 2), str(x), fill=label_color, font=font)
    for y in range(0, height, step):
        draw.text((2, y + 2), str(y), fill=label_color, font=font)

    base = img.convert("RGBA")
    return Image.alpha_composite(base, overlay).convert("RGB")


class CuaOpenUrl(Skill):
    name = "cua_open_url"
    description = (
        "Open a URL in the default browser via CUA desktop automation. "
        "Uses keyboard shortcut to open a new tab, types the URL, and presses Enter. "
        "Use this instead of cua_click to navigate to websites."
    )
    dangerous = True  # opening URLs can be risky
    untrusted = True  # content from external sites
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "url": {"type": "string", "description": "The URL to open."},
        },
        "required": ["url"],
    }

    def run(self, args: dict[str, Any]) -> str:
        if not _is_cua_enabled():
            return "error: CUA backend not enabled. Run 'halia setup --cua' first."

        url = args.get("url", "").strip()
        if not url:
            return "error: 'url' is required"

        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        try:
            import platform
            import shutil
            import subprocess
            import time

            # Open the URL in the default browser using the OS-native launcher.
            system = platform.system()
            if system == "Darwin":
                launcher = ["open", url]
            elif system == "Windows":
                launcher = ["cmd", "/c", "start", "", url]
            else:
                if shutil.which("xdg-open") is None:
                    return (
                        "error: no graphical browser launcher (xdg-open) found — "
                        "this looks like a headless system with no desktop. "
                        "Open the URL manually, or use browser/HTTP automation."
                    )
                launcher = ["xdg-open", url]

            subprocess.Popen(launcher)
            # Give the browser a moment to open the tab and start loading.
            time.sleep(1.5)

            return f"Opened {url} in default browser."
        except Exception as exc:
            return f"error: {exc}"


class CuaScreenshot(Skill):
    name = "cua_screenshot"
    description = (
        "Take a screenshot of the desktop. Captures the full screen via CUA driver. "
        "The screenshot is returned as an image the model can analyze visually, "
        "with a faint coordinate grid overlay so elements can be targeted "
        "precisely. Use this to see what's on screen before clicking or typing."
    )
    dangerous = False
    untrusted = False  # screenshots are read-only
    # Multi-modal: the tool result includes an image content block
    multi_modal = True
    # Side-channel: agent loop reads this after the tool runs
    _pending_image: str | None = None
    _pending_detail: str | None = None
    # Scale factor from the (resized) image the model sees back to real
    # screen pixels. Set on every screenshot; read by CuaClick/CuaScroll so
    # the model can give coordinates in image-space and we map them to the
    # real screen. Defaults to 1.0 (no scaling) until a screenshot is taken.
    _scale: float = 1.0
    # Keep the screenshot large enough to click precisely without exploding
    # image tokens. Native screens are usually 1920px wide.
    _MAX_WIDTH = 1600
    _JPEG_QUALITY = 90
    _GRID_STEP = 100
    _LOW_MAX_WIDTH = 800
    _LOW_JPEG_QUALITY = 70
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "grid": {
                "type": "boolean",
                "description": "Overlay a coordinate grid on the screenshot "
                "(default: true). Set false for a raw screenshot.",
            },
            "detail": {
                "type": "string",
                "enum": ["high", "low"],
                "description": "Resolution. 'high' (1600px) for precise "
                "targeting; 'low' (800px, smaller) for quick verification. "
                "Default: high.",
            },
        },
    }

    def run(self, args: dict[str, Any]) -> str:
        if not _is_cua_enabled():
            return "error: CUA backend not enabled. Run 'halia setup --cua' first."

        grid = args.get("grid", True)
        if not isinstance(grid, bool):
            grid = True

        detail = args.get("detail", "high")
        if detail not in ("high", "low"):
            detail = "high"

        try:
            import base64
            import io

            from PIL import Image

            cua = _get_cua()
            path = cua.screenshot()

            img: Image.Image = Image.open(path)
            real_w, real_h = img.size
            if detail == "low":
                max_width = CuaScreenshot._LOW_MAX_WIDTH
                quality = CuaScreenshot._LOW_JPEG_QUALITY
            else:
                max_width = CuaScreenshot._MAX_WIDTH
                quality = CuaScreenshot._JPEG_QUALITY
            if real_w > max_width:
                ratio = max_width / real_w
                img = img.resize(
                    (max_width, int(real_h * ratio)),
                    Image.Resampling.LANCZOS,
                )
            # Record how much the image was shrunk so clicks/scrolls can be
            # mapped from image-space back to real screen coordinates.
            CuaScreenshot._scale = real_w / img.size[0]
            if img.mode != "RGB":
                img = img.convert("RGB")
            if grid:
                img = _overlay_grid(img, step=CuaScreenshot._GRID_STEP)

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            CuaScreenshot._pending_image = base64.b64encode(
                buf.getvalue()
            ).decode("ascii")
            CuaScreenshot._pending_detail = detail
            return (
                f"Screenshot captured ({img.size[0]}x{img.size[1]}). "
                "Analyze the attached image. Give click/scroll coordinates in "
                "this image's pixel space — they are scaled to the real screen "
                "automatically."
            )
        except ImportError:
            try:
                import base64

                cua = _get_cua()
                path = cua.screenshot()
                img_bytes = Path(path).read_bytes()
                CuaScreenshot._scale = 1.0
                CuaScreenshot._pending_image = base64.b64encode(
                    img_bytes
                ).decode("ascii")
                CuaScreenshot._pending_detail = "low"
                return "Screenshot captured — analyze the attached image."
            except Exception as exc:
                return f"error: {exc}"
        except Exception as exc:
            return f"error: {exc}"


class CuaClick(Skill):
    name = "cua_click"
    description = (
        "Click at coordinates on the desktop via CUA driver. "
        "Works on any desktop element — native apps, browser, system UI. "
        "Use cua_screenshot first to see where to click."
    )
    dangerous = True  # clicking on desktop can be risky
    untrusted = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "x": {"type": "number", "description": "X coordinate to click."},
            "y": {"type": "number", "description": "Y coordinate to click."},
            "button": {
                "type": "string",
                "enum": ["left", "right", "middle"],
                "description": "Mouse button (default: left).",
            },
        },
        "required": ["x", "y"],
    }

    def run(self, args: dict[str, Any]) -> str:
        if not _is_cua_enabled():
            return "error: CUA backend not enabled. Run 'halia setup --cua' first."

        x = args.get("x")
        y = args.get("y")
        button = args.get("button", "left")

        if x is None or y is None:
            return "error: 'x' and 'y' coordinates are required"

        try:
            # Map from the (resized) image the model saw to real screen pixels.
            scale = CuaScreenshot._scale
            rx = float(x) * scale
            ry = float(y) * scale
            cua = _get_cua()
            result = cua.click(rx, ry, button)
            if scale != 1.0:
                result += f" [image {x},{y} -> screen {rx:.0f},{ry:.0f}]"
            return str(result)
        except Exception as exc:
            return f"error: {exc}"


class CuaDoubleClick(Skill):
    name = "cua_double_click"
    description = (
        "Double-click at coordinates on the desktop. Use this to OPEN files, "
        "folders, or apps on macOS/Windows (a single click only selects). "
        "Works on any desktop element. Use cua_screenshot first to see where "
        "to double-click."
    )
    dangerous = True
    untrusted = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "x": {"type": "number", "description": "X coordinate to double-click."},
            "y": {"type": "number", "description": "Y coordinate to double-click."},
            "button": {
                "type": "string",
                "enum": ["left", "right", "middle"],
                "description": "Mouse button (default: left).",
            },
        },
        "required": ["x", "y"],
    }

    def run(self, args: dict[str, Any]) -> str:
        if not _is_cua_enabled():
            return "error: CUA backend not enabled. Run 'halia setup --cua' first."

        x = args.get("x")
        y = args.get("y")
        button = args.get("button", "left")

        if x is None or y is None:
            return "error: 'x' and 'y' coordinates are required"

        try:
            scale = CuaScreenshot._scale
            rx = float(x) * scale
            ry = float(y) * scale
            cua = _get_cua()
            result = cua.double_click(rx, ry, button)
            if scale != 1.0:
                result += f" [image {x},{y} -> screen {rx:.0f},{ry:.0f}]"
            return str(result)
        except Exception as exc:
            return f"error: {exc}"


class CuaPressKey(Skill):
    name = "cua_press_key"
    description = (
        "Press a single key on the keyboard (e.g. 'return', 'enter', 'tab', "
        "'escape', 'delete', letters, digits). Use this to confirm a selection "
        "or trigger the focused control — select a file with cua_click, then "
        "press Return to open it. Do NOT type key names with cua_type."
    )
    dangerous = True
    untrusted = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "key": {
                "type": "string",
                "description": "Key to press (e.g. 'return', 'enter', 'tab', 'escape', 'a').",
            },
        },
        "required": ["key"],
    }

    def run(self, args: dict[str, Any]) -> str:
        if not _is_cua_enabled():
            return "error: CUA backend not enabled. Run 'halia setup --cua' first."

        key = args.get("key", "")
        if not key:
            return "error: 'key' is required"

        try:
            cua = _get_cua()
            return str(cua.press_key(key))
        except Exception as exc:
            return f"error: {exc}"


class CuaHotkey(Skill):
    name = "cua_hotkey"
    description = (
        "Press a keyboard shortcut (e.g. ['cmd', 'o'] to open a selected file, "
        "['cmd', 'w'] to close a window, ['cmd', 'tab'] to switch apps). On "
        "macOS use 'cmd'; on Windows/Linux use 'ctrl'."
    )
    dangerous = True
    untrusted = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "keys": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Keys to press together, e.g. ['cmd', 'o'].",
            },
        },
        "required": ["keys"],
    }

    def run(self, args: dict[str, Any]) -> str:
        if not _is_cua_enabled():
            return "error: CUA backend not enabled. Run 'halia setup --cua' first."

        keys = args.get("keys")
        if not isinstance(keys, list) or not keys:
            return "error: 'keys' (a list of strings) is required"

        try:
            cua = _get_cua()
            return str(cua.hotkey([str(k) for k in keys]))
        except Exception as exc:
            return f"error: {exc}"


class CuaType(Skill):
    """Type text into the focused element."""

    name = "cua_type"
    dangerous = True  # typing can interact with any app
    untrusted = False  # text comes from the model/user, not an external source
    description = (
        "Type text into the currently focused element. "
        "Click the field first to focus it, then type. "
        "Set clear=true to select-all + delete the field's existing "
        "content before typing — use this whenever you are re-filling or "
        "correcting a field that already has text, so you replace instead "
        "of appending."
    )
    parameters = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "text": {
                "type": "string",
                "description": "Text to type",
            },
            "clear": {
                "type": "boolean",
                "description": (
                    "Clear the field (select-all + delete) before typing. "
                    "Set true when the field already contains text you want "
                    "to replace."
                ),
            },
        },
        "required": ["text"],
    }

    def run(self, args: dict[str, Any]) -> str:
        if not _is_cua_enabled():
            return "error: CUA backend not enabled. Run 'halia setup --cua' first."

        text = args.get("text", "")
        clear = bool(args.get("clear", False))
        if not text:
            return "error: 'text' is required"

        try:
            cua = _get_cua()
            if clear:
                cua.clear_field()
            cua.type_text(text)
            return f"Typed {len(text)} characters." + (
                " (field cleared first)" if clear else ""
            )
        except Exception as exc:
            return f"error: {exc}"


class CuaScroll(Skill):
    name = "cua_scroll"
    description = (
        "Scroll the desktop at coordinates via CUA driver. "
        "Works on any scrollable element — browser, document viewer, etc."
    )
    dangerous = False
    untrusted = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "x": {"type": "number", "description": "X coordinate to scroll at."},
            "y": {"type": "number", "description": "Y coordinate to scroll at."},
            "direction": {
                "type": "string",
                "enum": ["up", "down", "left", "right"],
                "description": "Scroll direction (default: down).",
            },
            "amount": {
                "type": "integer",
                "description": "Scroll amount (default: 3).",
            },
        },
        "required": ["x", "y"],
    }

    def run(self, args: dict[str, Any]) -> str:
        if not _is_cua_enabled():
            return "error: CUA backend not enabled. Run 'halia setup --cua' first."

        x = args.get("x")
        y = args.get("y")
        direction = args.get("direction", "down")
        amount = args.get("amount", 3)

        if x is None or y is None:
            return "error: 'x' and 'y' coordinates are required"

        try:
            # Map from the (resized) image the model saw to real screen pixels.
            scale = CuaScreenshot._scale
            rx = float(x) * scale
            ry = float(y) * scale
            cua = _get_cua()
            result = cua.scroll(rx, ry, direction, amount)
            if scale != 1.0:
                result += f" [image {x},{y} -> screen {rx:.0f},{ry:.0f}]"
            return str(result)
        except Exception as exc:
            return f"error: {exc}"


class CuaDesktopState(Skill):
    name = "cua_desktop"
    description = (
        "Get the current desktop state via CUA driver. "
        "Returns information about visible UI elements and text. "
        "Use this to understand what's on screen before acting."
    )
    dangerous = False
    untrusted = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    }

    def run(self, args: dict[str, Any]) -> str:
        if not _is_cua_enabled():
            return "error: CUA backend not enabled. Run 'halia setup --cua' first."

        try:
            cua = _get_cua()
            return str(cua.desktop_state())
        except Exception as exc:
            return f"error: {exc}"
