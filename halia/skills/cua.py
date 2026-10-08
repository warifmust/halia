"""CUA (Computer Use Agent) skills — desktop automation via cua-driver.

Provides desktop-level automation skills that use the cua-driver SDK. CUA is
halia's only computer backend (browser automation has been removed), available
whenever a graphical display exists.

CUA skills can:
- Control any desktop application (not just a browser)
- Work in background without stealing focus
- Interact with native OS elements

Trust note: CUA operations are logged for audit but bypass halia's
filesystem guards (not applicable to desktop UI operations).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from halia.skills.base import Skill


def _is_cua_enabled() -> bool:
    """CUA is halia's only computer backend, so the tools are always enabled."""
    return True


def _get_cua() -> Any:
    """Get the CUA computer instance."""
    from halia.computer.cua_backend import cua_available, get_cua_computer
    if not cua_available():
        raise RuntimeError(
            "CUA desktop automation requires a graphical desktop "
            "(X11/Wayland on Linux, or a logged-in macOS/Windows session). "
            "This environment looks headless — use HTTP requests instead."
        )
    return get_cua_computer()


def _get_screenshots_dir() -> Path:
    """Screenshots directory: config `screenshot_dir`, or ~/.halia/screenshots/."""
    from halia.config.settings import CONFIG_DIR, read_config

    custom = read_config().get("screenshot_dir")
    if custom:
        return Path(str(custom)).expanduser()
    return CONFIG_DIR / "screenshots"


def _screenshot_dest() -> Path:
    """A fresh path in the screenshots dir for the next capture."""
    from datetime import UTC, datetime

    dest_dir = _get_screenshots_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    return dest_dir / f"screenshot-{stamp}.png"


def _screenshot_keep() -> int:
    """How many recent screenshots to retain (config `screenshot_keep`, default 100)."""
    from halia.config.settings import read_config

    try:
        keep = int(read_config().get("screenshot_keep", 100))
    except (TypeError, ValueError):
        keep = 100
    return max(1, keep)


def _prune_screenshots() -> None:
    """Keep only the most recent `screenshot_keep` screenshots on disk."""
    try:
        dest_dir = _get_screenshots_dir()
        if not dest_dir.is_dir():
            return
        keep = _screenshot_keep()
        files = sorted(
            (p for p in dest_dir.iterdir() if p.suffix.lower() == ".png"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for stale in files[keep:]:
            try:
                stale.unlink()
            except OSError:
                pass
    except OSError:
        pass


def _window_target_schema() -> dict[str, Any]:
    """Schema properties that let an action address a window instead of the desktop."""
    return {
        "pid": {
            "type": "integer",
            "description": "Process id of the target window's app — with window_id.",
        },
        "window_id": {
            "type": "integer",
            "description": "Window id from cua_desktop — with pid.",
        },
        "element_token": {
            "type": "string",
            "description": (
                "Element token from cua_window. Preferred over pixel coordinates: "
                "it needs no pixel maths and works on backgrounded, minimized, "
                "hidden and off-Space windows."
            ),
        },
        "element_index": {
            "type": "integer",
            "description": "Element index from cua_window — use with snapshot_id.",
        },
        "snapshot_id": {
            "type": "string",
            "description": "Snapshot id from cua_window — required with element_index.",
        },
    }


def _as_int(value: Any, field: str) -> int:
    """Coerce a tool argument to int, naming the field when it cannot be."""
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"'{field}' must be an integer, got {value!r}") from exc


def _window_target_args(args: dict[str, Any]) -> dict[str, Any]:
    """Collect the window-scoped addressing fields from a tool call's arguments.

    Both `pid` and `window_id` are needed to address a window, so a lone one is
    ignored and the call falls back to the desktop path rather than failing.
    """
    target: dict[str, Any] = {}
    raw_pid = args.get("pid")
    raw_window = args.get("window_id")
    if raw_pid is None or raw_window is None:
        return {}
    target["pid"] = _as_int(raw_pid, "pid")
    target["window_id"] = _as_int(raw_window, "window_id")
    for key in ("element_token", "snapshot_id"):
        value = args.get(key)
        if value:
            target[key] = str(value)
    if args.get("element_index") is not None:
        target["element_index"] = _as_int(args.get("element_index"), "element_index")
    return target


def _short_num(value: Any) -> str:
    """Format a coordinate without a trailing `.0`."""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(num)) if num.is_integer() else f"{num:g}"


def _window_header(data: dict[str, Any]) -> str:
    """Identity line for a get_window_state payload: window, title, pid, bounds."""
    head = f"window {data.get('window_id')}"
    title = str(data.get("window_title") or data.get("app_name") or "").strip()
    if title:
        head += f' "{title[:80]}"'
    if data.get("pid") is not None:
        head += f" (pid {data.get('pid')})"
    bounds = data.get("window_bounds")
    if isinstance(bounds, dict):
        width = bounds.get("width")
        height = bounds.get("height")
        if width is not None and height is not None:
            head += f" {_short_num(width)}x{_short_num(height)}"
            if bounds.get("x") is not None and bounds.get("y") is not None:
                head += (
                    f" at ({_short_num(bounds.get('x'))},"
                    f"{_short_num(bounds.get('y'))})"
                )
    snapshot = data.get("snapshot_id")
    if snapshot:
        head += f" snapshot={snapshot}"
    return head


def _format_window_state(raw: str) -> str:
    """Turn a get_window_state JSON payload into an actionable element listing.

    Each element becomes `[index] role "label" (x,y wxh) token=…`. Frames are left
    in the window's own coordinate space exactly as the driver reports them — never
    rescaled against `CuaScreenshot._scale`, which belongs to the desktop capture
    and would silently misplace every click aimed at a window.

    `token` is the driver's element handle: pass it to cua_click to act on that
    element with no coordinates at all, which also works while the window is
    backgrounded, minimized, hidden or on another Space.
    """
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return raw  # not JSON — return the driver's text as-is

    if not isinstance(data, dict):
        return raw

    elements = data.get("elements")
    if not isinstance(elements, list):
        return raw

    lines: list[str] = []
    for el in elements:
        if not isinstance(el, dict):
            continue
        idx = el.get("element_index")
        if idx is None:
            continue
        role = el.get("role") or "element"
        label = el.get("label")
        label_part = f' "{label}"' if label else ""
        frame_part = ""
        frame = el.get("frame")
        if isinstance(frame, dict):
            fx = frame.get("x")
            fy = frame.get("y")
            fw = frame.get("w", frame.get("width"))
            fh = frame.get("h", frame.get("height"))
            if None not in (fx, fy, fw, fh):
                frame_part = (
                    f" ({_short_num(fx)},{_short_num(fy)}"
                    f" {_short_num(fw)}x{_short_num(fh)})"
                )
        token = el.get("element_token")
        token_part = f" token={token}" if token else ""
        lines.append(f"[{idx}] {role}{label_part}{frame_part}{token_part}")

    header = _window_header(data)
    # The driver returns an EMPTY tree on purpose when it cannot prove which
    # accessibility surface belongs to this window, and refuses background input
    # while that holds — say so, or an empty listing reads as "no controls here".
    if data.get("degraded"):
        reason = str(
            data.get("degraded_reason") or "the accessibility surface is unresolved"
        )
        if len(reason) > 180:
            reason = reason[:180].rstrip() + "…"
        header += f"\n⚠ degraded: {reason}"

    header += f"\nelements: {len(lines)} returned"
    total = data.get("element_count")
    if total is not None:
        header += f" (of {total})"
    if not lines:
        return header + "\n(no elements returned)"
    return header + "\n" + "\n".join(lines)


def _summarize_apps(tree: str, max_chars: int = 2400) -> str:
    """Compact the get_accessibility_tree payload into a running-app inventory.

    Only app names and pids are kept. Window ids deliberately come from
    `_summarize_windows` instead: this tree lists only windows that are currently
    on screen, and its records are not a reliable source of a real window-server
    id — acting on one taken from here can silently target the wrong window.

    Apps are ordered frontmost-first when the tree exposes a frontmost/active
    flag, so the app the user is actually working in is never truncated away.
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

    def _is_frontmost(app: dict[str, Any]) -> bool:
        for key in ("frontmost", "is_frontmost", "active", "is_active"):
            val = app.get(key)
            if val is True or val == 1:
                return True
        return False

    # Stable sort: frontmost apps first, otherwise preserve the tree's order.
    apps = sorted(apps, key=lambda a: 0 if _is_frontmost(a) else 1)

    lines: list[str] = []
    omitted = 0
    for idx, app in enumerate(apps):
        if not isinstance(app, dict):
            continue
        name = app.get("name") or app.get("bundle_id") or "app"
        head = str(name)
        pid = app.get("pid")
        if pid is not None:
            head += f" (pid {pid})"
        # Stop before adding a line that would overflow the budget.
        if sum(len(line) + 1 for line in lines) + len(head) + 1 > max_chars:
            omitted = len(apps) - idx
            break
        lines.append(head)

    body = "\n".join(lines)
    summary = f"running apps ({len(apps)}):\n{_trunc(body)}"
    if omitted > 0:
        summary += f"\n(… {omitted} app(s) omitted — the list was truncated.)"
    return summary


def _summarize_windows(raw: str, max_chars: int = 2400) -> str:
    """Compact the list_windows payload into one line per app, with window ids.

    These are the authoritative window ids. `list_windows` reports every top-level
    window the window server knows about — including ones that are off-screen,
    minimized, hidden or on another Space or display — so a window id from here
    can be handed straight to cua_window or to a window-scoped cua_click even when
    the desktop capture cannot see that window.
    """
    def _trunc(text: str) -> str:
        return text if len(text) <= max_chars else text[:max_chars].rstrip() + "…"

    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return _trunc(raw)
    if not isinstance(data, dict):
        return _trunc(raw)
    windows = data.get("windows")
    if not isinstance(windows, list):
        return _trunc(raw)

    def _sort_key(w: dict[str, Any]) -> tuple[int, int]:
        """On-screen windows first, then front-to-back by z-order."""
        return (0 if w.get("is_on_screen") else 1, -(w.get("z_index") or 0))

    by_app: dict[str, list[dict[str, Any]]] = {}
    for w in windows:
        if isinstance(w, dict):
            by_app.setdefault(str(w.get("app_name") or "app"), []).append(w)

    ordered = sorted(by_app.items(), key=lambda kv: min(_sort_key(w) for w in kv[1]))
    lines: list[str] = []
    omitted_apps = 0
    for idx, (app, wins) in enumerate(ordered):
        wins.sort(key=_sort_key)
        pid = wins[0].get("pid")
        head = f"{app}" + (f" (pid {pid})" if pid is not None else "")
        parts: list[str] = []
        for w in wins[:8]:
            bounds = w.get("bounds")
            size = ""
            if isinstance(bounds, dict):
                width = bounds.get("width")
                height = bounds.get("height")
                if width is not None and height is not None:
                    size = f" {_short_num(width)}x{_short_num(height)}"
            title = str(w.get("title") or "").strip()
            title_part = f' "{title[:60]}"' if title else ""
            state = "on-screen" if w.get("is_on_screen") else "off-screen"
            parts.append(f"[{w.get('window_id')}]{title_part}{size} {state}")
        if len(wins) > 8:
            parts.append(f"(+{len(wins) - 8} more)")
        head += ": " + "; ".join(parts)
        if sum(len(line) + 1 for line in lines) + len(head) + 1 > max_chars:
            omitted_apps = len(ordered) - idx
            break
        lines.append(head)

    body = _trunc("\n".join(lines))
    summary = (
        f"windows ({len(windows)} total,"
        f" {sum(1 for w in windows if w.get('is_on_screen'))} on screen):\n{body}"
    )
    if omitted_apps > 0:
        summary += f"\n(… {omitted_apps} app(s) omitted — the list was truncated.)"
    return summary


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


def _region_box(args: dict[str, Any]) -> tuple[float, float, float, float] | None:
    """Optional (x, y, width, height) crop region in image space, or None.

    All four must be supplied together, in the same pixel space as click/scroll
    coordinates (the grid on the last screenshot). Zero or negative width/height is
    an error; coordinates may be partially out of bounds — they are clamped.
    """
    x_raw = args.get("x")
    y_raw = args.get("y")
    w_raw = args.get("width")
    h_raw = args.get("height")
    if x_raw is None and y_raw is None and w_raw is None and h_raw is None:
        return None
    if x_raw is None or y_raw is None or w_raw is None or h_raw is None:
        raise ValueError("region needs all of x, y, width and height together")
    x, y, width, height = (float(v) for v in (x_raw, y_raw, w_raw, h_raw))
    if width <= 0 or height <= 0:
        raise ValueError("region width and height must be positive")
    return (x, y, width, height)


class CuaScreenshot(Skill):
    name = "cua_screenshot"
    description = (
        "Take a screenshot of the PRIMARY display via CUA driver. Captures that "
        "display at full screen size — windows living on another display, or "
        "minimized or hidden, are not in frame; use cua_desktop to find them and "
        "cua_window(screenshot=true) to see one. "
        "The screenshot is returned as an image the model can analyze visually, "
        "with a faint coordinate grid overlay so elements can be targeted "
        "precisely. The full-resolution PNG is also saved to the screenshots "
        "directory. Use this to see what's on screen before clicking or typing. "
        "Use detail:'low' (1024px) for fast, shallow checks like navigation; "
        "use detail:'high' (1600px) when you need precise visual detail. "
        "For a close-up of ONE part — e.g. a success toast or a status message "
        "like 'deflected to Human Agent' — pass x, y, width and height to crop to "
        "that region (coordinates in the grid's pixel space); pass grid:false for "
        "a clean, evidence-ready capture. A crop is also saved to its own file "
        "(named …-crop.png), so the saved artifact is the close-up, not the page."
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
    _LOW_MAX_WIDTH = 1024
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
                "description": "Resolution + quality. 'high' = 1600px wide "
                "(precise, slower); 'low' = 1024px wide (faster, fewer image "
                "tokens). Coordinates are given in the image's own pixel space "
                "either way and are scaled to the real screen automatically. "
                "Default: high.",
            },
            "x": {
                "type": "number",
                "description": "Region crop: left edge, in the last screenshot's grid pixel "
                "space. Provide with y, width and height.",
            },
            "y": {
                "type": "number",
                "description": "Region crop: top edge, in the last screenshot's grid pixel "
                "space. Provide with x, width and height.",
            },
            "width": {
                "type": "number",
                "description": "Region crop: width, in the last screenshot's grid pixel space. "
                "Provide with x, y and height.",
            },
            "height": {
                "type": "number",
                "description": "Region crop: height, in the last screenshot's grid pixel space. "
                "Provide with x, y and width.",
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

            region = _region_box(args)
            cua = _get_cua()
            dest = _screenshot_dest()
            path = cua.screenshot(str(dest))
            _prune_screenshots()

            img: Image.Image = Image.open(path)
            real_w, real_h = img.size
            # `detail` trades resolution (and JPEG quality): 'high' keeps the full
            # 1600px image for precise visual work, 'low' shrinks to 1024px so
            # routine navigation uses fewer image tokens and encodes faster. The
            # scale factor is recomputed per screenshot, so click coordinates are
            # mapped from whichever image the model saw back to the real screen.
            max_width = (
                CuaScreenshot._MAX_WIDTH
                if detail == "high"
                else CuaScreenshot._LOW_MAX_WIDTH
            )
            quality = (
                CuaScreenshot._JPEG_QUALITY
                if detail == "high"
                else CuaScreenshot._LOW_JPEG_QUALITY
            )
            # image space → real screen pixels, before any resize.
            scale = real_w / max_width if real_w > max_width else 1.0

            region_note = ""
            is_crop = False
            if region is not None:
                # Region coordinates live in the LAST full screenshot's pixel space
                # (the grid the model read), exactly like click coordinates — so use
                # the persisted scale, not this call's `detail`. Otherwise a crop
                # ordered after a 'low' screenshot would be interpreted in 'high'
                # space and land on the wrong region.
                crop_scale = CuaScreenshot._scale
                x, y, w, h = region
                rx = int(round(x * crop_scale))
                ry = int(round(y * crop_scale))
                rw = int(round(w * crop_scale))
                rh = int(round(h * crop_scale))
                # Clamp to the real frame so an estimate slightly off-screen still
                # yields the visible part rather than an empty/error image.
                rx = max(0, min(rx, real_w - 1))
                ry = max(0, min(ry, real_h - 1))
                rw = max(1, min(rw, real_w - rx))
                rh = max(1, min(rh, real_h - ry))
                img = img.crop((rx, ry, rx + rw, ry + rh))
                # Save the crop itself to disk at native resolution — this is the
                # artifact QA asked for. The full capture stays at `dest`; the
                # result message reports the crop's path, not the full page's.
                crop_path = dest.with_name(f"{dest.stem}-crop.png")
                img.save(crop_path, format="PNG")
                # Legibility: downscale oversized crops to the cap, upscale small
                # ones 2x so a tiny toast stays readable. Never exceed max_width.
                crop_w, crop_h = img.size
                if crop_w > max_width:
                    ratio = max_width / crop_w
                    img = img.resize(
                        (max_width, int(crop_h * ratio)), Image.Resampling.LANCZOS
                    )
                elif crop_w < 800:
                    img = img.resize(
                        (crop_w * 2, crop_h * 2), Image.Resampling.LANCZOS
                    )
                region_note = (
                    f"Region crop of ({_short_num(x)},{_short_num(y)}) "
                    f"{_short_num(w)}x{_short_num(h)} in image space "
                    f"(≈{rx},{ry} {rw}x{rh} real px). "
                )
                is_crop = True
            else:
                if real_w > max_width:
                    ratio = max_width / real_w
                    img = img.resize(
                        (max_width, int(real_h * ratio)),
                        Image.Resampling.LANCZOS,
                    )
                # Record how much the image was shrunk so clicks/scrolls can be
                # mapped from image-space back to real screen coordinates.
                CuaScreenshot._scale = scale

            if img.mode != "RGB":
                img = img.convert("RGB")
            if grid:
                step = 50 if (is_crop and img.size[0] < 800) else CuaScreenshot._GRID_STEP
                img = _overlay_grid(img, step=step)

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            CuaScreenshot._pending_image = base64.b64encode(
                buf.getvalue()
            ).decode("ascii")
            CuaScreenshot._pending_detail = detail
            if is_crop:
                return (
                    f"{region_note}Captured ({img.size[0]}x{img.size[1]}). "
                    f"Saved crop to {crop_path}. "
                    "This is a crop for inspection/evidence — its coordinates are "
                    "NOT click coordinates. To click or scroll, take a full "
                    "cua_screenshot first (pass grid:false for a clean capture)."
                )
            return (
                f"Screenshot captured ({img.size[0]}x{img.size[1]}). Saved to {dest}. "
                "Analyze the attached image. Give click/scroll coordinates in "
                "this image's pixel space — they are scaled to the real screen "
                "automatically."
            )
        except ImportError:
            try:
                import base64

                cua = _get_cua()
                dest = _screenshot_dest()
                path = cua.screenshot(str(dest))
                _prune_screenshots()
                img_bytes = Path(path).read_bytes()
                CuaScreenshot._scale = 1.0
                CuaScreenshot._pending_image = base64.b64encode(
                    img_bytes
                ).decode("ascii")
                CuaScreenshot._pending_detail = "low"
                return f"Screenshot captured — saved to {dest}. Analyze the attached image."
            except Exception as exc:
                return f"error: {exc}"
        except Exception as exc:
            return f"error: {exc}"


class CuaClick(Skill):
    name = "cua_click"
    description = (
        "Click via the CUA driver. Window-scoped: pass pid + window_id with an "
        "element_token from cua_window (preferred — no coordinates needed), or x/y "
        "in that window's screenshot space; this also reaches windows on another "
        "display or Space. Desktop-scoped: pass x/y only, in your last "
        "cua_screenshot's pixel space."
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
            **_window_target_schema(),
        },
    }

    def run(self, args: dict[str, Any]) -> str:
        if not _is_cua_enabled():
            return "error: CUA backend not enabled. Run 'halia setup --cua' first."

        x = args.get("x")
        y = args.get("y")
        button = args.get("button", "left")

        try:
            cua = _get_cua()
            target = _window_target_args(args)
            if target:
                if not (target.get("element_token") or target.get("element_index")):
                    if x is None or y is None:
                        return (
                            "error: a window click needs element_token, "
                            "element_index, or both x and y"
                        )
                return str(cua.click(
                    None if x is None else float(x),
                    None if y is None else float(y),
                    button,
                    **target,
                ))
            if x is None or y is None:
                return "error: 'x' and 'y' coordinates are required"
            # Map from the (resized) image the model saw to real screen pixels.
            scale = CuaScreenshot._scale
            rx = float(x) * scale
            ry = float(y) * scale
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
            **_window_target_schema(),
        },
    }

    def run(self, args: dict[str, Any]) -> str:
        if not _is_cua_enabled():
            return "error: CUA backend not enabled. Run 'halia setup --cua' first."

        x = args.get("x")
        y = args.get("y")
        button = args.get("button", "left")

        try:
            cua = _get_cua()
            target = _window_target_args(args)
            if target:
                if not (target.get("element_token") or target.get("element_index")):
                    if x is None or y is None:
                        return (
                            "error: a window double-click needs element_token, "
                            "element_index, or both x and y"
                        )
                return str(cua.double_click(
                    None if x is None else float(x),
                    None if y is None else float(y),
                    button,
                    **target,
                ))
            if x is None or y is None:
                return "error: 'x' and 'y' coordinates are required"
            scale = CuaScreenshot._scale
            rx = float(x) * scale
            ry = float(y) * scale
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
            **_window_target_schema(),
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
            cua = _get_cua()
            target = _window_target_args(args)
            if target:
                # Window coordinates are passed through untouched: they are already
                # in the target window's screenshot space, not the desktop's.
                return str(cua.drag(
                    float(from_x), float(from_y), float(to_x), float(to_y),
                    button=button,
                    duration_ms=duration_ms, steps=steps,
                    **target,
                ))
            scale = CuaScreenshot._scale
            rfx = float(from_x) * scale
            rfy = float(from_y) * scale
            rtx = float(to_x) * scale
            rty = float(to_y) * scale
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


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Euclidean distance between two 2D points."""
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _catmull_rom(
    p0: tuple[float, float], p1: tuple[float, float],
    p2: tuple[float, float], p3: tuple[float, float], t: float,
) -> tuple[float, float]:
    """One Catmull-Rom spline point between p1 and p2 at parameter t in [0,1]."""
    t2 = t * t
    t3 = t2 * t
    x = 0.5 * (
        (2 * p1[0])
        + (-p0[0] + p2[0]) * t
        + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
        + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3
    )
    y = 0.5 * (
        (2 * p1[1])
        + (-p0[1] + p2[1]) * t
        + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
        + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3
    )
    return (x, y)


def _smooth_points(
    points: list[tuple[float, float]], step: float = 8.0,
) -> list[tuple[float, float]]:
    """Densify a polyline into a smooth curve with segments no longer than `step` px."""
    if len(points) < 3:
        return list(points)
    out: list[tuple[float, float]] = [points[0]]
    n = len(points)
    for i in range(n - 1):
        p0 = points[i - 1] if i > 0 else points[i]
        p1 = points[i]
        p2 = points[i + 1]
        p3 = points[i + 2] if i + 2 < n else points[i + 1]
        seg_len = _dist(p1, p2)
        samples = max(2, int(seg_len / step))
        for j in range(1, samples + 1):
            out.append(_catmull_rom(p0, p1, p2, p3, j / samples))
    return out


class CuaDrawPath(Skill):
    name = "cua_draw_path"
    description = (
        "Draw a freehand STROKE along a list of waypoints in ONE call. Pass "
        "`points` as [x, y] pairs in the LAST cua_screenshot's pixel space "
        "(they are scaled to the real screen automatically). The stroke is drawn "
        "as a chain of short drags through the waypoints, with optional smoothing "
        "so a few control points become one smooth curve. Use this to DRAW in "
        "AutoDraw, Canva, Preview, or any canvas — one call = one whole stroke, "
        "instead of many cua_drag calls."
    )
    dangerous = True
    untrusted = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "points": {
                "type": "array",
                "minItems": 2,
                "items": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "number"},
                },
                "description": "List of [x, y] waypoints (at least 2), in the "
                "screenshot's pixel space.",
            },
            "button": {
                "type": "string",
                "enum": ["left", "right", "middle"],
                "description": "Mouse button held during the stroke (default: left).",
            },
            "smooth": {
                "type": "boolean",
                "description": "Interpolate a smooth curve through the waypoints "
                "(default: true). Set false to draw straight segments.",
            },
            "duration_ms": {
                "type": "integer",
                "description": "Total stroke duration in milliseconds (optional; "
                "larger = slower, steadier stroke).",
            },
        },
        "required": ["points"],
    }

    def run(self, args: dict[str, Any]) -> str:
        if not _is_cua_enabled():
            return "error: CUA backend not enabled. Run 'halia setup --cua' first."

        raw_points = args.get("points")
        if not isinstance(raw_points, list) or len(raw_points) < 2:
            return "error: 'points' (at least 2 [x, y] pairs) is required"

        points: list[tuple[float, float]] = []
        for p in raw_points:
            if not isinstance(p, (list, tuple)) or len(p) != 2:
                return "error: each point must be an [x, y] pair"
            try:
                points.append((float(p[0]), float(p[1])))
            except (TypeError, ValueError):
                return "error: point coordinates must be numbers"

        button = args.get("button", "left")
        smooth = bool(args.get("smooth", True))
        duration_ms = args.get("duration_ms")
        if duration_ms is not None:
            duration_ms = int(duration_ms)

        if smooth:
            points = _smooth_points(points)

        scale = CuaScreenshot._scale
        real = [(x * scale, y * scale) for x, y in points]
        seg_count = len(real) - 1
        if seg_count < 1:
            return "error: need at least 2 distinct points"
        per_seg = duration_ms // seg_count if duration_ms else None

        try:
            cua = _get_cua()
            for i in range(seg_count):
                x1, y1 = real[i]
                x2, y2 = real[i + 1]
                cua.drag(x1, y1, x2, y2, button=button, duration_ms=per_seg, steps=1)
        except Exception as exc:
            return f"error: {exc}"

        first = raw_points[0]
        last = raw_points[-1]
        return (
            f"Drew a stroke through {len(points)} points ({seg_count} segments) "
            f"[image {first[0]},{first[1]} -> {last[0]},{last[1]} -> screen "
            f"{real[0][0]:.0f},{real[0][1]:.0f} -> {real[-1][0]:.0f},{real[-1][1]:.0f}]"
        )


def _fill_spans(
    pts: list[tuple[float, float]],
    spacing: float,
    direction: str,
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Rasterize a closed polygon into parallel fill strokes (even-odd rule).

    Returns a list of ``((x1, y1), (x2, y2))`` drag spans in the SAME coordinate
    space as `pts` (callers pass real-screen points). `direction` is 'horizontal'
    (scan y, fill along x) or 'vertical' (scan x, fill along y). The outline is
    implicitly closed with a last→first edge.
    """
    n = len(pts)
    if n < 3:
        return []

    spans: list[tuple[tuple[float, float], tuple[float, float]]] = []
    if direction == "horizontal":
        lo = min(p[1] for p in pts)
        hi = max(p[1] for p in pts)
        s = lo
        while s <= hi + 1e-9:
            xs: list[float] = []
            for i in range(n):
                (x1, y1), (x2, y2) = pts[i], pts[(i + 1) % n]
                ylo, yhi = min(y1, y2), max(y1, y2)
                if yhi - ylo < 1e-12:
                    continue  # horizontal edge — never crosses
                if s < ylo or s >= yhi:
                    continue  # half-open [ylo, yhi)
                xs.append(x1 + (s - y1) / (y2 - y1) * (x2 - x1))
            xs.sort()
            for k in range(0, len(xs) - 1, 2):
                if xs[k + 1] - xs[k] >= 1e-9:
                    spans.append(((xs[k], s), (xs[k + 1], s)))
            s += spacing
    else:  # vertical
        lo = min(p[0] for p in pts)
        hi = max(p[0] for p in pts)
        s = lo
        while s <= hi + 1e-9:
            ys: list[float] = []
            for i in range(n):
                (x1, y1), (x2, y2) = pts[i], pts[(i + 1) % n]
                xlo, xhi = min(x1, x2), max(x1, x2)
                if xhi - xlo < 1e-12:
                    continue  # vertical edge — never crosses
                if s < xlo or s >= xhi:
                    continue  # half-open [xlo, xhi)
                ys.append(y1 + (s - x1) / (x2 - x1) * (y2 - y1))
            ys.sort()
            for k in range(0, len(ys) - 1, 2):
                if ys[k + 1] - ys[k] >= 1e-9:
                    spans.append(((s, ys[k]), (s, ys[k + 1])))
            s += spacing
    return spans


class CuaFillPath(Skill):
    name = "cua_fill_path"
    description = (
        "Fill a CLOSED shape with the currently selected color by dragging the "
        "pointer back and forth inside it (parallel scanlines). Give the outline "
        "as the list of [x, y] corner points (>= 3) in the screenshot's pixel "
        "space. A thicker brush fills faster and more solidly — set `spacing` to "
        "the brush width or less so the strokes overlap. Select the color first, "
        "then call this with the shape's outline. Use cua_screenshot before and "
        "after to check the result."
    )
    dangerous = True
    untrusted = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "points": {
                "type": "array",
                "minItems": 3,
                "items": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "number"},
                },
                "description": "The closed outline as a list of [x, y] corner "
                "points, in the screenshot's pixel space.",
            },
            "spacing": {
                "type": "number",
                "description": "Distance between fill strokes in pixels "
                "(default 5). Set to the brush width or less for a solid fill.",
            },
            "direction": {
                "type": "string",
                "enum": ["horizontal", "vertical"],
                "description": "Direction of the fill strokes (default: horizontal).",
            },
            "button": {
                "type": "string",
                "enum": ["left", "right", "middle"],
                "description": "Mouse button held during the fill (default: left).",
            },
            "duration_ms": {
                "type": "integer",
                "description": "Per-stroke duration in milliseconds (optional).",
            },
        },
        "required": ["points"],
    }

    def run(self, args: dict[str, Any]) -> str:
        if not _is_cua_enabled():
            return "error: CUA backend not enabled. Run 'halia setup --cua' first."

        raw_points = args.get("points")
        if not isinstance(raw_points, list) or len(raw_points) < 3:
            return "error: 'points' (a closed outline, >= 3 [x, y] pairs) is required"

        pts: list[tuple[float, float]] = []
        for p in raw_points:
            if not isinstance(p, (list, tuple)) or len(p) != 2:
                return "error: each point must be an [x, y] pair"
            try:
                pts.append((float(p[0]), float(p[1])))
            except (TypeError, ValueError):
                return "error: point coordinates must be numbers"

        spacing = float(args.get("spacing", 5.0))
        spacing = max(1.0, spacing)
        direction = args.get("direction", "horizontal")
        if direction not in ("horizontal", "vertical"):
            direction = "horizontal"
        button = args.get("button", "left")
        duration_ms = args.get("duration_ms")
        if duration_ms is not None:
            duration_ms = int(duration_ms)

        scale = CuaScreenshot._scale
        real = [(x * scale, y * scale) for x, y in pts]
        spans = _fill_spans(real, spacing, direction)
        if not spans:
            return "error: could not rasterize the shape — is the outline degenerate?"

        # Cap runaway fills (very large region + tiny spacing).
        max_strokes = 600
        if len(spans) > max_strokes:
            return (
                f"error: fill would need {len(spans)} strokes (>{max_strokes}). "
                f"Increase 'spacing' (or use a thicker brush) and retry."
            )

        try:
            cua = _get_cua()
            for (x1, y1), (x2, y2) in spans:
                cua.drag(
                    x1, y1, x2, y2,
                    button=button,
                    duration_ms=duration_ms,
                    steps=1,
                )
        except Exception as exc:
            return f"error: {exc}"

        xmin = min(p[0] for p in pts)
        ymin = min(p[1] for p in pts)
        return (
            f"Filled the shape with {len(spans)} {direction} strokes "
            f"(spacing {spacing:.1f}px) near image ({xmin:.0f},{ymin:.0f})."
        )


class CuaUndo(Skill):
    name = "cua_undo"
    description = (
        "Undo the last action (Cmd+Z on macOS, Ctrl+Z elsewhere). Use after a "
        "mistake in a drawing/editing app to revert one step; pass times=N to "
        "undo several steps at once."
    )
    dangerous = True
    untrusted = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "times": {
                "type": "integer",
                "description": "Number of undo steps (default: 1).",
            },
        },
    }

    def run(self, args: dict[str, Any]) -> str:
        if not _is_cua_enabled():
            return "error: CUA backend not enabled. Run 'halia setup --cua' first."

        try:
            times = int(args.get("times", 1) or 1)
        except (TypeError, ValueError):
            times = 1
        times = max(1, min(10, times))

        import sys
        mod = "cmd" if sys.platform == "darwin" else "ctrl"

        try:
            cua = _get_cua()
            for _ in range(times):
                cua.hotkey([mod, "z"])
            return f"Pressed {mod}+z {times} time(s)."
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
        "Type text into a field. Window-scoped: pass pid + window_id (with an "
        "element_token from cua_window to pick the field) to type into a window on "
        "any display, even backgrounded. Desktop-scoped: click the field first to "
        "focus it, then type. "
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
            **_window_target_schema(),
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
            target = _window_target_args(args)
            if target:
                if clear:
                    return (
                        "error: clear=true is not supported for a window target — "
                        "select-all would act on whatever the desktop has focused, "
                        "not on window "
                        f"{target.get('window_id')}. Clear the field first (focus "
                        "it, cua_hotkey cmd+a, cua_press_key delete), then type."
                    )
                return str(cua.type_text(text, **target))
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
        "Scroll via the CUA driver. Defaults to a medium step — about 20 lines or "
        "wheel notches — enough to move the view without skipping content. Use "
        "by:'page' for a big jump down a long document, by:'line' with a small "
        "amount to nudge when close to a target. Window-scoped: pass pid + window_id, "
        "with an element_token from cua_window or x/y in that window's screenshot "
        "space — this is how you scroll a specific window, including one on another "
        "display or in the background. Desktop-scoped: pass x/y only, scrolling "
        "whatever the desktop has focused."
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
            "by": {
                "type": "string",
                "enum": ["page", "line"],
                "description": (
                    "Step size. 'line' (default) is a medium chunk — a few lines or "
                    "wheel notches. 'page' is a full-viewport jump, for leaping down "
                    "a long document; it can skip past what you're looking for."
                ),
            },
            "amount": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "description": (
                    "How many steps. Default 20 (with by='line'), or 1 when "
                    "by='page'. Clamped to 1-50."
                ),
            },
            **_window_target_schema(),
        },
    }

    def run(self, args: dict[str, Any]) -> str:
        if not _is_cua_enabled():
            return "error: CUA backend not enabled. Run 'halia setup --cua' first."

        x = args.get("x")
        y = args.get("y")
        direction = args.get("direction", "down")
        from halia.computer.cua_backend import SCROLL_BY_LINE

        by = args.get("by", SCROLL_BY_LINE)
        # Absent amount lets the backend apply the per-granularity default.
        amount = args.get("amount")
        amount = None if amount is None else int(amount)

        try:
            cua = _get_cua()
            target = _window_target_args(args)
            if target:
                if not target.get("element_token") and (x is None or y is None):
                    return (
                        "error: a window scroll needs element_token, or both x "
                        "and y (in that window's screenshot space)"
                    )
                return str(cua.scroll(
                    None if x is None else float(x),
                    None if y is None else float(y),
                    direction,
                    amount,
                    by,
                    **target,
                ))
            if x is None or y is None:
                return "error: 'x' and 'y' coordinates are required"
            # Map from the (resized) image the model saw to real screen pixels.
            scale = CuaScreenshot._scale
            rx = float(x) * scale
            ry = float(y) * scale
            result = cua.scroll(rx, ry, direction, amount, by)
            if scale != 1.0:
                result += f" [image {x},{y} -> screen {rx:.0f},{ry:.0f}]"
            return str(result)
        except Exception as exc:
            return f"error: {exc}"


class CuaDesktopState(Skill):
    name = "cua_desktop"
    description = (
        "Get the current desktop state: screen size, running apps, and every "
        "top-level window with its pid and window_id. Includes off-screen windows "
        "— minimized, hidden, or on another Space or display — so this is how you "
        "find a window cua_screenshot cannot show. Then use cua_window(pid, "
        "window_id) for that window's controls."
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
            windows = cua.list_windows()
            if windows:
                parts.append(_summarize_windows(windows))
            tree = cua.accessibility_tree()
            if tree:
                parts.append(_summarize_apps(tree))
                hint = _security_dialog_hint(tree)
                if hint:
                    parts.append(hint)
            return "\n".join(parts)
        except Exception as exc:
            return f"error: {exc}"


class CuaWindow(Skill):
    name = "cua_window"
    description = (
        "Get one window's UI controls from the macOS accessibility tree. Pass the "
        "pid and window_id from cua_desktop. Each element lists its index, role, "
        "label, frame and token — pass the token to cua_click to act on that "
        "element with no coordinates. Works on windows the desktop capture cannot "
        "show (minimized, hidden, another Space or display). Add screenshot=true to "
        "also see the window itself."
    )
    dangerous = False
    untrusted = False
    # Multi-modal: `screenshot=true` stages a window image for the agent loop
    multi_modal = True
    _pending_image: str | None = None
    _pending_detail: str | None = None
    # Hash of the last staged window image, so an unchanged window is detected.
    _last_hash: str | None = None
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
            "screenshot": {
                "type": "boolean",
                "description": (
                    "Also capture this window and attach it as an image (default "
                    "false). Use it to see a window cua_screenshot cannot show — "
                    "one on another display or Space, or minimized."
                ),
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

        dest = _screenshot_dest() if args.get("screenshot") else None

        try:
            cua = _get_cua()
            raw = cua.window_state(
                int(pid), int(window_id),
                max_elements=max_elements, max_depth=max_depth,
                screenshot_out_file=str(dest) if dest else None,
            )
            listing = _format_window_state(raw)
        except Exception as exc:
            detail = str(exc)
            lowered = detail.lower()
            if "not a live window" in lowered or "stale" in lowered or "closed" in lowered:
                return (
                    f"error: {detail} — the pid/window_id is stale (the window "
                    "closed or moved). Re-run cua_desktop to get fresh pid/"
                    "window_id values, then retry cua_window with those."
                )
            return f"error: {detail}"

        if dest is None:
            return listing
        return f"{listing}\n{self._stage_window_image(dest)}"

    @staticmethod
    def _stage_window_image(dest: Path) -> str:
        """Attach the captured window PNG to the model's next observation.

        The image is passed through at the driver's own size — no halia-side
        resize — so pixel coordinates read off it stay in the same space the
        window's pixel actions expect.
        """
        import base64

        try:
            import io

            from PIL import Image

            _prune_screenshots()
            if not dest.is_file():
                return f"(no screenshot was written to {dest})"
            img: Image.Image = Image.open(dest)
            width, height = img.size
            if img.mode != "RGB":
                img = img.convert("RGB")
            img = _overlay_grid(img, step=CuaScreenshot._GRID_STEP)
            buf = io.BytesIO()
            img.save(
                buf,
                format="JPEG",
                quality=CuaScreenshot._JPEG_QUALITY,
                optimize=True,
            )
            CuaWindow._pending_image = base64.b64encode(buf.getvalue()).decode("ascii")
            CuaWindow._pending_detail = "high"
            return f"Window screenshot attached ({width}x{height}). Saved to {dest}."
        except ImportError:
            _prune_screenshots()
            if not dest.is_file():
                return f"(no screenshot was written to {dest})"
            CuaWindow._pending_image = base64.b64encode(dest.read_bytes()).decode("ascii")
            CuaWindow._pending_detail = "high"
            return f"Window screenshot attached. Saved to {dest}."
        except Exception as exc:  # noqa: BLE001 — the listing alone is still useful
            return f"(window screenshot unavailable: {exc})"
