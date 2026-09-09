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

import json
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


def _format_window_state(raw: str) -> str:
    """Turn a get_window_state JSON payload into a clickable element listing.

    Each element becomes `[index] role "label" -> click (cx, cy)` where the center
    is given in the LAST cua_screenshot's pixel space (divided by CuaScreenshot._scale),
    so the model can pass those numbers straight to cua_click.
    """
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return raw  # not JSON — return the driver's text as-is

    if not isinstance(data, dict):
        return raw

    total = data.get("element_count")
    elements = data.get("elements")
    if not isinstance(elements, list):
        return raw

    scale = CuaScreenshot._scale
    lines: list[str] = []
    for el in elements:
        if not isinstance(el, dict):
            continue
        frame = el.get("frame")
        if not isinstance(frame, dict):
            continue
        fx = frame.get("x")
        fy = frame.get("y")
        fw = frame.get("w", frame.get("width"))
        fh = frame.get("h", frame.get("height"))
        if fx is None or fy is None or fw is None or fh is None:
            continue
        try:
            x = float(fx)
            y = float(fy)
            w = float(fw)
            h = float(fh)
        except (TypeError, ValueError):
            continue
        cx = round((x + w / 2) / scale, 1)
        cy = round((y + h / 2) / scale, 1)
        idx = el.get("element_index")
        role = el.get("role") or "element"
        label = el.get("label")
        label_part = f' "{label}"' if label else ""
        lines.append(f"[{idx}] {role}{label_part} -> click ({cx}, {cy})")

    header = f"window elements: {len(lines)} returned"
    if total is not None:
        header += f" (of {total})"
    if scale != 1.0:
        header += f" — coords are in your last screenshot's pixel space (scale x{scale:.2f})"
    if not lines:
        return header + "\n(no elements with a frame)"
    return header + "\n" + "\n".join(lines)


def _summarize_desktop_tree(tree: str, max_chars: int = 1200) -> str:
    """Compact the get_accessibility_tree payload into app/window name+id lines.

    The raw tree lists every running app (and its windows) as JSON — large and
    mostly redundant. Keep the parts the model needs (app name, pid, window_id)
    and drop the bulk, so cua_desktop stays cheap enough to call repeatedly.
    """
    def _trunc(raw: str) -> str:
        return raw if len(raw) <= max_chars else raw[:max_chars].rstrip() + "…"

    try:
        data = json.loads(tree)
    except (ValueError, TypeError):
        return _trunc(tree)
    if not isinstance(data, dict):
        return _trunc(tree)
    apps = data.get("apps")
    if not isinstance(apps, list):
        return _trunc(tree)

    lines: list[str] = []
    for app in apps:
        if not isinstance(app, dict):
            continue
        name = app.get("name") or app.get("bundle_id") or "app"
        head = str(name)
        pid = app.get("pid")
        if pid is not None:
            head += f" (pid {pid})"
        windows = app.get("windows")
        if isinstance(windows, list):
            wins: list[str] = []
            for w in windows:
                if not isinstance(w, dict):
                    continue
                wid = w.get("window_id")
                if wid is None:
                    wid = w.get("id")
                title = w.get("title") or ""
                if wid is not None:
                    wins.append(f"win {wid} {title}".strip())
                elif title:
                    wins.append(str(title))
            if wins:
                head += ": " + ", ".join(wins[:8])
        lines.append(head)
        if sum(len(line) + 1 for line in lines) > max_chars:
            break
    body = "\n".join(lines)
    return f"running apps ({len(apps)}):\n{_trunc(body)}"


# Known names of OS security dialogs (credential / permission / elevation prompts)
# in the accessibility tree. High-signal only — never arbitrary page text, which
# would false-positive on login forms.
_SECURITY_DIALOG_MARKERS = (
    "securityagent",  # macOS authorization / password prompts
    "touch id",
    "keychain",
    "consent.exe",  # Windows UAC elevation prompt
)


def _security_dialog_hint(tree: str) -> str | None:
    """Flag a likely OS security dialog from the accessibility-tree payload.

    Scans app/window NAMES (never arbitrary page text) for known security-dialog
    markers. Returns a warning string if one is found, else None. Best-effort:
    it never blocks — it just makes the model pause instead of clicking into a
    credential / permission dialog it must not touch.
    """
    try:
        data = json.loads(tree)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    apps = data.get("apps")
    if not isinstance(apps, list):
        return None

    for app in apps:
        if not isinstance(app, dict):
            continue
        candidates = [
            str(app.get("name") or ""),
            str(app.get("bundle_id") or ""),
        ]
        windows = app.get("windows")
        if isinstance(windows, list):
            for w in windows:
                if isinstance(w, dict):
                    candidates.append(str(w.get("title") or ""))
        for candidate in candidates:
            lowered = candidate.lower()
            for marker in _SECURITY_DIALOG_MARKERS:
                if marker in lowered:
                    return (
                        "⚠️ SECURITY DIALOG DETECTED: a system credential/permission "
                        f"prompt may be open ('{candidate[:60]}'). STOP — do NOT click "
                        "into it, dismiss it, or type anything. Ask the user to "
                        "handle it."
                    )
    return None


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
        "Open a WEB URL (http/https) in the system's default browser. "
        "Do NOT use this for local files or folders — to open a file, folder, "
        "or app, use the desktop tools instead (cua_click to select, "
        "cua_double_click or cua_press_key 'return' to open, cua_hotkey "
        "['cmd','shift','g'] to go to a path, or ['cmd','space'] for Spotlight)."
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

        # This tool opens WEB pages only. A filesystem path (file://, ~/, /…)
        # must be handled by the desktop tools, not routed into a browser.
        lower = url.lower()
        if lower.startswith(("file:", "file://")):
            return (
                "error: cua_open_url opens web URLs (http/https) only. "
                f"'{url}' is a local file/folder — open it with cua_double_click "
                "or cua_hotkey (e.g. ['cmd','shift','g'] to go to a folder in Finder)."
            )
        if url.startswith(("/", "~", ".")) and "://" not in url:
            return (
                f"error: '{url}' looks like a local path, not a web URL. "
                "Use cua_double_click or cua_hotkey for files and folders."
            )

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
    # Hash of the last staged screenshot — used to detect an UNCHANGED screen.
    _last_hash: str | None = None
    # Scale factor from the (fixed-width) image the model sees back to real
    # screen pixels. Set on every screenshot; read by the coordinate tools
    # (click/scroll/drag/window) so the model can give coordinates in
    # image-space and we map them to the real screen. Because every screenshot
    # is the same width, this only changes when the actual screen size changes.
    _scale: float = 1.0
    # Screenshots are ALWAYS resized to this fixed width so the image the model
    # sees has a stable coordinate space. The scale factor (real screen px →
    # image px) is therefore constant for a given screen size, which keeps click
    # coordinates valid regardless of how many screenshots are taken or in what
    # order. Native screens are usually 1920px wide.
    _MAX_WIDTH = 1600
    _JPEG_QUALITY = 90
    _LOW_JPEG_QUALITY = 70
    _GRID_STEP = 100
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
                "description": "Image quality. Screenshots are always 1600px "
                "wide (the coordinate space never changes); 'low' only uses "
                "heavier JPEG compression for a smaller file. Default: high.",
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
            # One fixed width for every screenshot: the coordinate space the model
            # sees must not change between calls, or click coordinates break.
            # `detail` only trades JPEG quality (file size), never resolution.
            max_width = CuaScreenshot._MAX_WIDTH
            quality = (
                CuaScreenshot._JPEG_QUALITY
                if detail == "high"
                else CuaScreenshot._LOW_JPEG_QUALITY
            )
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


class CuaDrag(Skill):
    name = "cua_drag"
    description = (
        "Drag the mouse from one point to another while holding the button down. "
        "Use this to DRAW strokes on a canvas (AutoDraw, Preview, any drawing app): "
        "one drag draws one straight line segment, and you can chain several drags "
        "to sketch shapes — a triangle is 3 drags, a rectangle is 4. Use "
        "cua_screenshot first to see the start and end points, and give coordinates "
        "in the SCREENSHOT image's pixel space (halia scales them to the real screen)."
    )
    dangerous = True
    untrusted = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "from_x": {"type": "number", "description": "Start X coordinate."},
            "from_y": {"type": "number", "description": "Start Y coordinate."},
            "to_x": {"type": "number", "description": "End X coordinate."},
            "to_y": {"type": "number", "description": "End Y coordinate."},
            "button": {
                "type": "string",
                "enum": ["left", "right", "middle"],
                "description": "Mouse button held during the drag (default: left).",
            },
            "duration_ms": {
                "type": "integer",
                "description": "How long the drag takes in milliseconds — larger "
                "values draw slower, smoother strokes (optional).",
            },
            "steps": {
                "type": "integer",
                "description": "Intermediate points along the path — more steps make "
                "the stroke smoother (optional).",
            },
        },
        "required": ["from_x", "from_y", "to_x", "to_y"],
    }

    def run(self, args: dict[str, Any]) -> str:
        if not _is_cua_enabled():
            return "error: CUA backend not enabled. Run 'halia setup --cua' first."

        from_x = args.get("from_x")
        from_y = args.get("from_y")
        to_x = args.get("to_x")
        to_y = args.get("to_y")
        button = args.get("button", "left")
        duration_ms = args.get("duration_ms")
        if duration_ms is not None:
            duration_ms = int(duration_ms)
        steps = args.get("steps")
        if steps is not None:
            steps = int(steps)

        if from_x is None or from_y is None or to_x is None or to_y is None:
            return "error: 'from_x', 'from_y', 'to_x' and 'to_y' are required"

        try:
            scale = CuaScreenshot._scale
            rfx = float(from_x) * scale
            rfy = float(from_y) * scale
            rtx = float(to_x) * scale
            rty = float(to_y) * scale
            cua = _get_cua()
            result = cua.drag(
                rfx, rfy, rtx, rty,
                button=button,
                duration_ms=duration_ms,
                steps=steps,
            )
            if scale != 1.0:
                result += (
                    f" [image {from_x},{from_y} -> {to_x},{to_y} -> "
                    f"screen {rfx:.0f},{rfy:.0f} -> {rtx:.0f},{rty:.0f}]"
                )
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
        "Get the current desktop state: screen size, running apps, and visible "
        "windows (each with its pid and window_id). Then use cua_window(pid, "
        "window_id) to get a window's UI elements with clickable coordinates. "
        "Use this for precise targeting when pixel-guessing keeps missing."
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
            parts = [str(cua.desktop_state())]
            tree = cua.accessibility_tree()
            if tree:
                parts.append(_summarize_desktop_tree(tree))
                hint = _security_dialog_hint(tree)
                if hint:
                    parts.append(hint)
            return "\n".join(parts)
        except Exception as exc:
            return f"error: {exc}"


class CuaWindow(Skill):
    name = "cua_window"
    description = (
        "Get a window's UI elements with their clickable coordinates (from the "
        "macOS accessibility tree). Pass the pid and window_id returned by "
        "cua_desktop. Each element lists its index, role, label, and click center "
        "— in the same pixel space as the last cua_screenshot, so the centers can "
        "be passed straight to cua_click. Use this instead of pixel-guessing."
    )
    dangerous = False
    untrusted = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "pid": {"type": "integer", "description": "Process id of the window's app."},
            "window_id": {"type": "integer", "description": "Window id from cua_desktop."},
            "max_elements": {
                "type": "integer",
                "description": "Cap the number of elements returned (default 200).",
            },
            "max_depth": {
                "type": "integer",
                "description": "Cap the accessibility-tree depth (optional).",
            },
        },
        "required": ["pid", "window_id"],
    }

    def run(self, args: dict[str, Any]) -> str:
        if not _is_cua_enabled():
            return "error: CUA backend not enabled. Run 'halia setup --cua' first."

        pid = args.get("pid")
        window_id = args.get("window_id")
        if pid is None or window_id is None:
            return "error: 'pid' and 'window_id' are required"

        max_elements = args.get("max_elements")
        if max_elements is not None:
            max_elements = int(max_elements)
        max_depth = args.get("max_depth")
        if max_depth is not None:
            max_depth = int(max_depth)

        try:
            cua = _get_cua()
            raw = cua.window_state(
                int(pid), int(window_id),
                max_elements=max_elements, max_depth=max_depth,
            )
            return _format_window_state(raw)
        except Exception as exc:
            return f"error: {exc}"
