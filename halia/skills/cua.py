"""CUA (Computer Use Agent) skills — desktop automation via cua-driver.

Provides desktop-level automation skills that use the cua-driver SDK.
These skills are only available when computer_backend == "cua".

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
    """Check if CUA backend is enabled."""
    from halia.config.settings import read_config
    config = read_config()
    return config.get("computer_backend") == "cua"


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
        "The screenshot is returned as an image the model can analyze visually. "
        "Use this to see what's on screen before clicking or typing."
    )
    dangerous = False
    untrusted = False  # screenshots are read-only
    # Multi-modal: the tool result includes an image content block
    multi_modal = True
    # Side-channel: agent loop reads this after the tool runs
    _pending_image: str | None = None
    # Scale factor from the (resized) image the model sees back to real
    # screen pixels. Set on every screenshot; read by CuaClick/CuaScroll so
    # the model can give coordinates in image-space and we map them to the
    # real screen. Defaults to 1.0 (no scaling) until a screenshot is taken.
    _scale: float = 1.0
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    }

    def run(self, args: dict[str, Any]) -> str:
        if not _is_cua_enabled():
            return "error: CUA backend not enabled. Run 'halia setup --cua' first."

        try:
            import base64
            import io

            from PIL import Image

            cua = _get_cua()
            path = cua.screenshot()

            img: Image.Image = Image.open(path)
            real_w, real_h = img.size
            max_width = 800
            if real_w > max_width:
                ratio = max_width / real_w
                img = img.resize(
                    (max_width, int(real_h * ratio)), Image.Resampling.LANCZOS
                )
            # Record how much the image was shrunk so clicks/scrolls can be
            # mapped from image-space back to real screen coordinates.
            CuaScreenshot._scale = real_w / img.size[0]
            if img.mode != "RGB":
                img = img.convert("RGB")

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=40, optimize=True)
            CuaScreenshot._pending_image = base64.b64encode(
                buf.getvalue()
            ).decode("ascii")
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
