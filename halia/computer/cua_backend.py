"""CUA Driver backend — thin wrapper around cua-driver SDK.

Provides desktop automation via cua-driver for halia's computer skills.
Screenshots, clicks, typing, and desktop state — all via cua-driver.

Usage:
    from halia.computer.cua_backend import CuaComputer
    computer = CuaComputer()
    await computer.screenshot()
    await computer.click(100, 200)
"""

from __future__ import annotations

import asyncio
import atexit
import base64
import json
import logging
import os
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# The version halia installs and re-applies on every install/upgrade: the one that
# has been validated end to end. Pinned EXACTLY rather than as a range so no
# re-install can silently swap the driver for a build nobody has run — including
# the newer-but-compatible 0.30–0.33 line, which shares the 3-field signature but
# has never been exercised. Change this only after testing the new version.
CUA_DRIVER_SPEC = "==0.29.1"

# The band halia can still DRIVE, used by the session guard. Wider than the install
# pin on purpose: a deliberately-installed in-band driver is accepted rather than
# blocked, while 0.34.0+ is refused.
CUA_DRIVER_MIN_VERSION = "0.29"
CUA_DRIVER_MAX_VERSION = "0.34"  # exclusive
CUA_DRIVER_RANGE = f">={CUA_DRIVER_MIN_VERSION},<{CUA_DRIVER_MAX_VERSION}"


def _version_tuple(text: str) -> tuple[int, ...]:
    """Parse a release version into comparable integers ('0.29.1' → (0, 29, 1))."""
    parts: list[int] = []
    for chunk in str(text).split("."):
        digits = ""
        for char in chunk:
            if not char.isdigit():
                break
            digits += char
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def runtime_cua_driver_version() -> str | None:
    """The installed cua-driver version, or None when it is not installed."""
    import importlib.metadata as metadata

    try:
        return metadata.version("cua-driver")
    except metadata.PackageNotFoundError:
        return None


def cua_driver_supported(version: str) -> bool:
    """Whether halia can start a session against this cua-driver version."""
    parsed = _version_tuple(version)
    return (
        parsed >= _version_tuple(CUA_DRIVER_MIN_VERSION)
        and parsed < _version_tuple(CUA_DRIVER_MAX_VERSION)
    )


def cua_driver_repair_hint() -> str:
    """The command that pins cua-driver back into halia's supported range."""
    return (
        f"uv pip install --python {sys.executable} 'cua-driver{CUA_DRIVER_SPEC}'"
    )


def check_cua_driver_version() -> str | None:
    """Return a repair-oriented message when the installed driver is unsupported.

    The pin is what keeps halia working: 0.34.0 requires a `cursor_motion` field
    halia never passes, so an upgraded driver breaks every cua_* tool with an
    opaque TypeError. Anything that re-resolves the environment (`uv tool install
    --force`, a manual `uv pip install cua-driver`) can drift past the cap, so the
    session refuses to start and says exactly how to get back.
    """
    version = runtime_cua_driver_version()
    if version is None or cua_driver_supported(version):
        return None
    return (
        f"cua-driver {version} is outside halia's supported range "
        f"({CUA_DRIVER_RANGE}). Version {CUA_DRIVER_MAX_VERSION}.0 requires a "
        "`cursor_motion` field halia does not pass, so every cua_* tool would fail "
        "at session start. Pin it back:\n"
        f"  {cua_driver_repair_hint()}\n"
        "or reinstall with `halia setup --cua`, which installs a supported version."
    )


def cua_available() -> bool:
    """Whether CUA desktop automation can run in this environment.

    CUA drives a real desktop through the native window server. On headless
    Linux there is no display, so the driver cannot load its X11 libraries or
    open windows — return False so callers can fall back instead of surfacing
    cryptic `libXi.so.6` / `xdg-open` failures.
    """
    from halia.computer import display_available
    return display_available()


def _cua_capture_scope() -> Any | None:
    """Resolve the CUA capture scope from env/config; None → driver default.

    `window` scopes the agent's input to a single window so the user can keep
    working in other windows; `desktop` drives the whole desktop; `auto` lets
    the driver choose. Set HALIA_CUA_CAPTURE_SCOPE or config `cua_capture_scope`.
    """
    from halia.config.settings import read_config

    raw = os.environ.get("HALIA_CUA_CAPTURE_SCOPE") or read_config().get("cua_capture_scope")
    if not raw:
        return None
    from cua_driver import CaptureScope

    key = str(raw).strip().lower()
    if key == "window":
        return CaptureScope.WINDOW
    if key == "desktop":
        return CaptureScope.DESKTOP
    if key == "auto":
        return CaptureScope.AUTO
    return None


def _cua_cursor_theme() -> Any | None:
    """Resolve the CUA agent-cursor theme from env/config; default → `cua.default`.

    The driver renders its own cursor overlay; `theme_id` selects its appearance
    (so it can be visually distinct from the user's real pointer). The only
    built-in theme is `cua.default` — the blue CUA cursor. Set HALIA_CUA_CURSOR_THEME
    or config `cua_cursor_theme` to select an installed custom theme instead.
    """
    from halia.config.settings import read_config

    theme_id = os.environ.get("HALIA_CUA_CURSOR_THEME") or read_config().get("cua_cursor_theme")
    if not theme_id:
        theme_id = "cua.default"
    from cua_driver import CursorReducedMotion, CursorThemeSelection

    return CursorThemeSelection(
        theme_id=str(theme_id).strip(),
        reduced_motion=CursorReducedMotion.AUTO,
    )


def _cua_input_delivery() -> Any:
    """Resolve the click input delivery mode from env/config.

    FOREGROUND moves the real system cursor to the target (user-like); BACKGROUND
    injects events without moving the user's cursor, so halia's overlay cursor acts
    independently and the user can keep using their mouse. Defaults to FOREGROUND
    for maximum app compatibility. Set HALIA_CUA_INPUT_MODE=background (or config
    `cua_input_mode`) to separate the cursors.
    """
    from cua_driver import InputDeliveryMode
    from halia.config.settings import read_config

    raw = os.environ.get("HALIA_CUA_INPUT_MODE") or read_config().get("cua_input_mode")
    if str(raw or "").strip().lower() == "background":
        return InputDeliveryMode.BACKGROUND
    return InputDeliveryMode.FOREGROUND


# The macOS bundle identity the embedded cua-driver binary runs under. TCC grants
# (Accessibility/Screen Recording) are attached to this identity by
# `cua-driver permissions grant`, so both the overlay cursor and screen capture
# work. Override with HALIA_CUA_HOST_BUNDLE_ID for a custom install.
_CUA_HOST_BUNDLE_ID = os.environ.get("HALIA_CUA_HOST_BUNDLE_ID", "com.trycua.driver")


class CuaComputer:
    """Desktop automation via cua-driver SDK."""

    def __init__(self) -> None:
        self._driver: Any = None
        self._host: Any = None
        self._session_name = "halia"
        self._lock = threading.Lock()
        self._session_started = False

    async def _ensure_driver(self) -> Any:
        """Lazy-start the embedded cua-driver host and connect to it.

        The embedded host launches the bundled `cua-driver` binary (same version
        as the SDK) as a subprocess with the overlay cursor enabled
        (`no_overlay=False`), then we connect to its socket. The in-process
        `CuaDriver.create()` runtime performs input and capture but never renders
        the visual agent cursor — the overlay lives in the binary, so we run it.
        """
        if self._driver is not None:
            return self._driver

        try:
            from cua_driver import (
                CuaDriver,
                EmbeddedCuaDriverHost,
                EmbeddedDriverHostOptions,
                get_binary_path,
            )
        except ImportError as exc:
            raise RuntimeError(
                "cua-driver is not installed. "
                "Run `halia setup --cua` to install it."
            ) from exc

        host = EmbeddedCuaDriverHost.with_options(
            EmbeddedDriverHostOptions(
                binary_path=str(get_binary_path()),
                host_bundle_id=_CUA_HOST_BUNDLE_ID,
                socket_path=None,
                startup_timeout_ms=None,
                shutdown_timeout_ms=None,
                permission_mode=None,
                session_policy_path=None,
                approve_session_policy=False,
                dangerously_bypass_approvals=False,
                environment=[],
                inherit_stderr=False,
                no_overlay=False,
            )
        )
        try:
            connection = await host.start()
            self._driver = CuaDriver.connect(connection.socket_path)
        except Exception:
            try:
                await host.stop()
            except Exception:
                pass
            raise
        self._host = host
        return self._driver

    def _run_async(self, coro: Any) -> Any:
        """Run an async coroutine in a new event loop (for sync skill context)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # Already in an event loop — run in a thread
            with self._lock:
                result = None
                exc: BaseException | None = None

                def _target() -> None:
                    nonlocal result, exc
                    try:
                        result = asyncio.run(coro)
                    except BaseException as e:
                        exc = e

                t = threading.Thread(target=_target)
                t.start()
                t.join(timeout=120)

                if exc is not None:
                    raise exc
                return result
        else:
            return asyncio.run(coro)

    def _is_session_ended(self, message: str) -> bool:
        """Detect the cua-driver 'session ended' error (from a message or error object)."""
        text = (message or "").lower()
        return "session has ended" in text or "call start_session" in text

    def _is_connection_dead(self, message: str) -> bool:
        """Detect a dead embedded-host connection (the child binary got killed).

        The embedded ``cua-driver`` binary can die out from under us (Ctrl-C in
        the terminal reaches the child through the process group). Every call then
        fails with a socket error — "Connection refused", "No such file or
        directory", etc. That needs a HOST relaunch, not just a session restart.
        """
        text = (message or "").lower()
        return any(
            kw in text
            for kw in (
                "connection refused",
                "connection reset",
                "broken pipe",
                "connection closed",
                "not connected",
                "no such file or directory",
                "connect to ",
            )
        )

    async def _reset_driver(self) -> None:
        """Drop a dead embedded host so the next `_ensure_driver` starts fresh.

        Stop the (already dead) host, clear the cached driver/host/session, and
        let the next operation relaunch the binary and reconnect.
        """
        host, self._host = self._host, None
        self._driver = None
        self._session_started = False
        if host is not None:
            try:
                await host.stop()
            except Exception:  # noqa: BLE001 — the host is already dead; ignore
                pass

    async def _ensure_session(self) -> Any:
        """Ensure we have an active CUA session (started once, then reused)."""
        driver = await self._ensure_driver()
        if self._session_started:
            return driver
        problem = check_cua_driver_version()
        if problem is not None:
            raise RuntimeError(problem)
        from cua_driver import StartSessionInput

        await driver.start_session(
            StartSessionInput(
                session=self._session_name,
                capture_scope=_cua_capture_scope(),
                cursor_theme=_cua_cursor_theme(),
            )
        )
        self._session_started = True
        await self._enable_agent_cursor(driver)
        return driver

    async def _enable_agent_cursor(self, driver: Any) -> None:
        """Make the driver's agent-cursor overlay visible for this session.

        The overlay is the distinct blue CUA cursor that lets the user keep
        working alongside halia while it drives the desktop. The driver enables
        it by default, but the embedded host can differ — turn it on explicitly.
        Never fatal: an unsupported driver or a host without an overlay event
        loop must not break the whole session.
        """
        try:
            from cua_driver import SetAgentCursorEnabledInput

            result = await driver.set_agent_cursor_enabled(
                SetAgentCursorEnabledInput(session=self._session_name, enabled=True)
            )
            if getattr(result, "is_error", False):
                logger.warning(
                    "CUA agent cursor could not be enabled: %s",
                    getattr(result, "text", "") or getattr(result, "error_code", ""),
                )
        except Exception as exc:  # noqa: BLE001 — cosmetic; never fail a session over it
            logger.warning("CUA agent cursor enable failed: %s", exc)

    async def _with_session_retry(self, op: Any) -> Any:
        """Run a driver operation, restarting the session once if it has ended.

        The cua-driver session can end out from under us (timeout, crash, or a
        previous ``close()``). When it does, every ``get_desktop_state``/input
        call fails with "this session has ended". Without recovery the agent is
        stuck — it retries dead tools and drifts to whatever still "works".
        Reset the flag, call ``start_session`` again, and retry once.
        """
        last_exc: BaseException | None = None
        for _ in range(2):
            try:
                driver = await self._ensure_session()
                result = await op(driver)
                # The driver sometimes returns an error object instead of raising.
                if getattr(result, "is_error", False):
                    detail = getattr(result, "text", "") or getattr(result, "error_code", "")
                    raise RuntimeError(str(detail).rstrip())
                return result
            except Exception as exc:  # noqa: BLE001 — retry once, then re-raise
                last_exc = exc
                if self._is_session_ended(str(exc)):
                    # Session ended — drop the stale flag so the next attempt restarts.
                    self._session_started = False
                elif self._is_connection_dead(str(exc)):
                    # The embedded host died — relaunch it on the next attempt.
                    await self._reset_driver()
                else:
                    raise
        assert last_exc is not None  # the loop always runs at least once
        raise last_exc

    async def _screenshot_async(self, path: str | None = None) -> str:
        """Take a desktop screenshot via cua-driver."""
        from cua_driver import GetDesktopStateInput

        async def _get(driver: Any) -> Any:
            return await driver.get_desktop_state(
                GetDesktopStateInput(
                    session=self._session_name,
                    screenshot_out_file=None,
                )
            )

        desktop = await self._with_session_retry(_get)

        if not (hasattr(desktop, "images") and desktop.images):
            detail = getattr(desktop, "text", "") or "no screenshot returned"
            raise RuntimeError(f"CUA returned no screenshot: {detail}".rstrip())

        # Save screenshot
        if path:
            screenshot_path = Path(path).expanduser()
        else:
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            screenshot_path = Path(tmp.name)
            tmp.close()

        img = desktop.images[0]
        # CUA stores images as base64-encoded data
        if hasattr(img, "data_base64") and img.data_base64:
            screenshot_path.write_bytes(base64.b64decode(img.data_base64))
        elif hasattr(img, "data") and img.data:
            screenshot_path.write_bytes(img.data)
        elif hasattr(img, "url") and img.url:
            import httpx
            resp = httpx.get(img.url)
            screenshot_path.write_bytes(resp.content)
        else:
            raise RuntimeError("CUA returned image with no data")

        return str(screenshot_path)

    @staticmethod
    def _brief(result: str, limit: int = 300) -> str:
        """Collapse a driver result into one short confirmation line."""
        text = " ".join(str(result).split())
        if not text:
            return ""
        return text if len(text) <= limit else text[:limit].rstrip() + "…"

    async def _window_action_async(self, tool: str, arguments: dict[str, Any]) -> str:
        """Run a window-scoped action through the driver's tool channel.

        Window actions go through `call_tool` rather than the typed SDK inputs so
        the argument names match the driver's published schemas and so element
        addressing (`element_token`, or `element_index` + `snapshot_id`), which has
        no typed equivalent, stays available.
        """
        payload: dict[str, Any] = {"session": self._session_name, **arguments}
        result = await self._call_tool_async(tool, payload)
        return self._brief(self._tool_result_to_str(result))

    async def _click_async(
        self,
        x: float | None = None,
        y: float | None = None,
        button: str = "left",
        count: int = 1,
        *,
        pid: int | None = None,
        window_id: int | None = None,
        element_token: str | None = None,
        element_index: int | None = None,
        snapshot_id: str | None = None,
    ) -> str:
        """Click via cua-driver (count=2 for a double-click).

        Window-scoped when `pid` + `window_id` are given, which reaches windows
        the desktop capture cannot: prefer `element_token`, because it needs no
        coordinates and works while the window is backgrounded, minimized, hidden
        or on another Space. `x`/`y` are then in that window's screenshot pixel
        space. Without `pid`/`window_id` the click is desktop-scoped and `x`/`y`
        are screen pixels on the primary display.
        """
        verb = "Double-clicked" if count >= 2 else "Clicked"

        if pid is not None and window_id is not None:
            arguments: dict[str, Any] = {
                "pid": int(pid),
                "window_id": int(window_id),
                "button": button,
                "count": count,
            }
            if element_token:
                arguments["element_token"] = element_token
                where = f"element {element_token}"
            elif element_index is not None:
                arguments["element_index"] = int(element_index)
                if snapshot_id:
                    arguments["snapshot_id"] = snapshot_id
                where = f"element {element_index}"
            elif x is not None and y is not None:
                arguments["x"] = float(x)
                arguments["y"] = float(y)
                where = f"({x}, {y})"
            else:
                return (
                    "error: a window click needs element_token, element_index, "
                    "or x/y"
                )
            detail = await self._window_action_async("click", arguments)
            result = f"{verb} {button} in window {window_id} at {where}"
            return f"{result} — {detail}" if detail else result

        if x is None or y is None:
            return "error: a desktop click needs x and y"

        from cua_driver import (
            ActionTarget,
            ClickButton,
            ClickInput,
            ClickPosition,
        )

        # Map string button name to enum
        btn_map = {
            "left": ClickButton.LEFT,
            "right": ClickButton.RIGHT,
            "middle": ClickButton.MIDDLE,
        }
        btn = btn_map.get(button, ClickButton.LEFT)

        async def _op(driver: Any) -> Any:
            return await driver.click(
                ClickInput(
                    target=ActionTarget.DESKTOP("primary"),
                    position=ClickPosition.COORDINATES(x, y),
                    delivery_mode=_cua_input_delivery(),
                    session=self._session_name,
                    button=btn,
                    count=count,
                )
            )

        await self._with_session_retry(_op)
        return f"{verb} {button} at ({x}, {y})"

    async def _type_async(
        self,
        text: str,
        *,
        pid: int | None = None,
        window_id: int | None = None,
        element_token: str | None = None,
        element_index: int | None = None,
        snapshot_id: str | None = None,
    ) -> str:
        """Type text via cua-driver.

        Window-scoped when `pid` + `window_id` are given: the write goes to the
        element named by `element_token`/`element_index`, or to that window's
        focused field when neither is given. Without them it types into whatever
        the desktop currently has focused.
        """
        preview = f"{text[:50]}{'...' if len(text) > 50 else ''}"

        if pid is not None and window_id is not None:
            arguments: dict[str, Any] = {
                "pid": int(pid),
                "window_id": int(window_id),
                "text": text,
            }
            if element_token:
                arguments["element_token"] = element_token
            elif element_index is not None:
                arguments["element_index"] = int(element_index)
                if snapshot_id:
                    arguments["snapshot_id"] = snapshot_id
            detail = await self._window_action_async("type_text", arguments)
            result = f"Typed into window {window_id}: {preview}"
            return f"{result} — {detail}" if detail else result

        from cua_driver import DesktopScope, TypeTextInput

        async def _op(driver: Any) -> Any:
            return await driver.type_text(
                TypeTextInput(
                    session=self._session_name,
                    text=text,
                    target=None,
                    scope=DesktopScope.DESKTOP,
                )
            )

        await self._with_session_retry(_op)
        return f"Typed: {preview}"

    async def _scroll_async(
        self,
        x: float | None = None,
        y: float | None = None,
        direction: str = "down",
        amount: int = 3,
        *,
        pid: int | None = None,
        window_id: int | None = None,
        element_token: str | None = None,
    ) -> str:
        """Scroll via cua-driver.

        Window-scoped when `pid` + `window_id` are given, which is what scrolls a
        specific window rather than whatever the desktop has focused: `element_token`
        targets one element, or `x`/`y` roll the wheel at a point in that window's
        screenshot space — the only way to scroll a nested scrollable region.
        """
        if pid is not None and window_id is not None:
            arguments: dict[str, Any] = {
                "pid": int(pid),
                "window_id": int(window_id),
                "direction": direction,
                "amount": amount,
                "by": "line",
            }
            if element_token:
                arguments["element_token"] = element_token
            elif x is not None and y is not None:
                arguments["x"] = float(x)
                arguments["y"] = float(y)
            detail = await self._window_action_async("scroll", arguments)
            result = f"Scrolled {direction} in window {window_id}"
            return f"{result} — {detail}" if detail else result

        from cua_driver import DesktopScope, ScrollBy, ScrollDirection, ScrollInput

        if x is None or y is None:
            x = y = 0.0

        async def _op(driver: Any) -> Any:
            return await driver.scroll(
                ScrollInput(
                    session=self._session_name,
                    x=x,
                    y=y,
                    direction=(
                        ScrollDirection.DOWN if direction == "down" else ScrollDirection.UP
                    ),
                    target=None,
                    scope=DesktopScope.DESKTOP,
                    by=ScrollBy.LINE,
                    amount=amount,
                )
            )

        await self._with_session_retry(_op)
        return f"Scrolled {direction} at ({x}, {y})"

    async def _drag_async(
        self,
        from_x: float,
        from_y: float,
        to_x: float,
        to_y: float,
        button: str = "left",
        duration_ms: int | None = None,
        steps: int | None = None,
        modifier: list[str] | None = None,
        *,
        pid: int | None = None,
        window_id: int | None = None,
    ) -> str:
        """Drag from (from_x, from_y) to (to_x, to_y) via cua-driver.

        Presses the button at the start point, moves through `steps` intermediate
        points over `duration_ms`, then releases — a continuous stroke. This is how
        you DRAW: one drag = one line segment; chain several to sketch shapes.

        Window-scoped when `pid` + `window_id` are given, where all four
        coordinates are in that window's screenshot pixel space; otherwise the
        drag runs against the primary display.
        """
        if pid is not None and window_id is not None:
            arguments: dict[str, Any] = {
                "pid": int(pid),
                "window_id": int(window_id),
                "from_x": from_x,
                "from_y": from_y,
                "to_x": to_x,
                "to_y": to_y,
                "button": button,
            }
            if duration_ms is not None:
                arguments["duration_ms"] = duration_ms
            if steps is not None:
                arguments["steps"] = steps
            if modifier:
                arguments["modifier"] = modifier
            detail = await self._window_action_async("drag", arguments)
            result = (
                f"Dragged in window {window_id} from ({from_x}, {from_y}) "
                f"to ({to_x}, {to_y})"
            )
            return f"{result} — {detail}" if detail else result

        from cua_driver import ActionTarget, ClickButton, DragInput

        btn_map = {
            "left": ClickButton.LEFT,
            "right": ClickButton.RIGHT,
            "middle": ClickButton.MIDDLE,
        }
        btn = btn_map.get(button, ClickButton.LEFT)

        async def _op(driver: Any) -> Any:
            return await driver.drag(
                DragInput(
                    from_x=from_x,
                    from_y=from_y,
                    to_x=to_x,
                    to_y=to_y,
                    target=ActionTarget.DESKTOP("primary"),
                    scope=None,
                    session=self._session_name,
                    duration_ms=duration_ms,
                    steps=steps,
                    button=btn,
                    modifier=modifier,
                )
            )

        await self._with_session_retry(_op)
        return f"Dragged from ({from_x}, {from_y}) to ({to_x}, {to_y})"

    async def _desktop_state_async(self) -> str:
        """Get full desktop state via cua-driver."""
        from cua_driver import GetDesktopStateInput

        async def _get(driver: Any) -> Any:
            return await driver.get_desktop_state(
                GetDesktopStateInput(
                    session=self._session_name,
                    screenshot_out_file=None,
                )
            )

        desktop = await self._with_session_retry(_get)

        # Build a readable state description
        parts = ["Desktop state:"]
        if hasattr(desktop, "images") and desktop.images:
            parts.append(f"  Screenshot: {len(desktop.images)} image(s)")
        if hasattr(desktop, "elements"):
            parts.append(f"  UI elements: {len(desktop.elements)}")
        if hasattr(desktop, "text"):
            parts.append(f"  Visible text: {desktop.text[:200]}")

        return "\n".join(parts)

    async def _desktop_state_json_async(self) -> str:
        """Return the raw desktop-state JSON (element tree) from cua-driver."""
        from cua_driver import GetDesktopStateInput

        async def _get(driver: Any) -> Any:
            return await driver.get_desktop_state(
                GetDesktopStateInput(
                    session=self._session_name,
                    screenshot_out_file=None,
                )
            )

        desktop = await self._with_session_retry(_get)
        sections = []
        text = getattr(desktop, "text", None)
        structured = getattr(desktop, "structured_json", None)
        raw = getattr(desktop, "raw_json", None)
        if text:
            sections.append(f"== text ==\n{text}")
        if structured:
            sections.append(f"== structured_json ==\n{structured}")
        if raw:
            sections.append(f"== raw_json ==\n{raw}")
        if not sections:
            return str(desktop)
        return "\n\n".join(sections)

    async def _call_tool_async(self, name: str, arguments: dict[str, Any]) -> Any:
        """Invoke a named cua-driver tool via the generic `call_tool` bridge.

        The Python SDK exposes the core inputs (click, drag, …) directly, but the
        accessibility tools (`get_accessibility_tree`, `get_window_state`) are only
        reachable through the generic call_tool(name, arguments_json) channel.
        """

        async def _op(driver: Any) -> Any:
            return await driver.call_tool(name, json.dumps(arguments))

        return await self._with_session_retry(_op)

    @staticmethod
    def _tool_result_to_str(result: Any) -> str:
        """Prefer the structured JSON of a tool result; fall back to its text."""
        structured = getattr(result, "structured_json", None)
        if structured:
            return str(structured)
        text = getattr(result, "text", None)
        return str(text or "")

    async def _accessibility_tree_async(self) -> str:
        """Return the desktop's accessibility tree summary: apps + visible windows."""
        result = await self._call_tool_async("get_accessibility_tree", {})
        return self._tool_result_to_str(result)

    async def _list_windows_async(self) -> str:
        """Enumerate every top-level window the window server knows about.

        Unlike `get_accessibility_tree`, this includes off-screen windows —
        minimized, hidden-launched, and on another Space or display — and gives
        the real window-server id needed to target one. That makes it the only
        reliable source of the `pid`/`window_id` pair `_window_state_async` takes.
        """
        result = await self._call_tool_async("list_windows", {})
        return self._tool_result_to_str(result)

    async def _window_state_async(
        self,
        pid: int,
        window_id: int,
        max_elements: int | None = None,
        max_depth: int | None = None,
        screenshot_out_file: str | None = None,
    ) -> str:
        """Return a window's UI element tree (roles, labels, frames) as JSON.

        Pass `screenshot_out_file` to have the driver write the window's own PNG
        there. A window capture resolves by window id, so it works for windows the
        desktop capture cannot reach — another display, another Space, or hidden.
        """
        arguments: dict[str, Any] = {"pid": pid, "window_id": window_id}
        if max_elements is not None:
            arguments["max_elements"] = max_elements
        if max_depth is not None:
            arguments["max_depth"] = max_depth
        # Never take the inline base64 PNG — it is ~1 MB per call. A caller that
        # wants the image passes `screenshot_out_file` and the driver writes it
        # to disk instead.
        arguments["include_screenshot"] = False
        if screenshot_out_file is not None:
            arguments["screenshot_out_file"] = screenshot_out_file
        result = await self._call_tool_async("get_window_state", arguments)
        return self._tool_result_to_str(result)

    async def _hotkey_async(self, keys: list[str]) -> str:
        """Press a hotkey combination via cua-driver."""
        from cua_driver import DesktopScope, HotkeyInput

        async def _op(driver: Any) -> Any:
            return await driver.hotkey(
                HotkeyInput(
                    session=self._session_name,
                    keys=keys,
                    target=None,
                    scope=DesktopScope.DESKTOP,
                )
            )

        await self._with_session_retry(_op)
        return f"Pressed hotkey: {'+'.join(keys)}"

    async def _press_key_async(self, key: str, modifiers: list[str] | None = None) -> str:
        """Press a single key via cua-driver."""
        from cua_driver import DesktopScope, PressKeyInput

        async def _op(driver: Any) -> Any:
            return await driver.press_key(
                PressKeyInput(
                    session=self._session_name,
                    key=key,
                    target=None,
                    scope=DesktopScope.DESKTOP,
                    modifiers=modifiers or [],
                )
            )

        await self._with_session_retry(_op)
        return f"Pressed key: {key}"

    async def _clear_field_async(self) -> str:
        """Select-all then delete, clearing the focused text field."""
        mod = "cmd" if sys.platform == "darwin" else "ctrl"
        await self._hotkey_async([mod, "a"])
        await self._press_key_async("delete")
        return "Field cleared (select-all + delete)."

    # ── Sync wrappers ──────────────────────────────────────────────────────

    def screenshot(self, path: str | None = None) -> str:
        """Take a desktop screenshot (sync wrapper)."""
        return str(self._run_async(self._screenshot_async(path)))

    def click(
        self,
        x: float | None = None,
        y: float | None = None,
        button: str = "left",
        *,
        pid: int | None = None,
        window_id: int | None = None,
        element_token: str | None = None,
        element_index: int | None = None,
        snapshot_id: str | None = None,
    ) -> str:
        """Click (sync wrapper). Window-scoped when `pid` + `window_id` are given."""
        return str(self._run_async(self._click_async(
            x, y, button,
            pid=pid,
            window_id=window_id,
            element_token=element_token,
            element_index=element_index,
            snapshot_id=snapshot_id,
        )))

    def double_click(
        self,
        x: float | None = None,
        y: float | None = None,
        button: str = "left",
        *,
        pid: int | None = None,
        window_id: int | None = None,
        element_token: str | None = None,
        element_index: int | None = None,
        snapshot_id: str | None = None,
    ) -> str:
        """Double-click (sync wrapper). Window-scoped when `pid` + `window_id` are given."""
        return str(self._run_async(self._click_async(
            x, y, button, count=2,
            pid=pid,
            window_id=window_id,
            element_token=element_token,
            element_index=element_index,
            snapshot_id=snapshot_id,
        )))

    def type_text(
        self,
        text: str,
        *,
        pid: int | None = None,
        window_id: int | None = None,
        element_token: str | None = None,
        element_index: int | None = None,
        snapshot_id: str | None = None,
    ) -> str:
        """Type text (sync wrapper). Window-scoped when `pid` + `window_id` are given."""
        return str(self._run_async(self._type_async(
            text,
            pid=pid,
            window_id=window_id,
            element_token=element_token,
            element_index=element_index,
            snapshot_id=snapshot_id,
        )))

    def scroll(
        self,
        x: float | None = None,
        y: float | None = None,
        direction: str = "down",
        amount: int = 3,
        *,
        pid: int | None = None,
        window_id: int | None = None,
        element_token: str | None = None,
    ) -> str:
        """Scroll (sync wrapper). Window-scoped when `pid` + `window_id` are given."""
        return str(self._run_async(self._scroll_async(
            x, y, direction, amount,
            pid=pid,
            window_id=window_id,
            element_token=element_token,
        )))

    def drag(
        self,
        from_x: float,
        from_y: float,
        to_x: float,
        to_y: float,
        button: str = "left",
        duration_ms: int | None = None,
        steps: int | None = None,
        modifier: list[str] | None = None,
        *,
        pid: int | None = None,
        window_id: int | None = None,
    ) -> str:
        """Drag from one point to another (sync wrapper)."""
        return str(self._run_async(
            self._drag_async(
                from_x, from_y, to_x, to_y, button, duration_ms, steps, modifier,
                pid=pid,
                window_id=window_id,
            )
        ))

    def desktop_state(self) -> str:
        """Get desktop state (sync wrapper)."""
        return str(self._run_async(self._desktop_state_async()))

    def desktop_state_json(self) -> str:
        """Get the raw desktop-state JSON (element tree) — sync wrapper."""
        return str(self._run_async(self._desktop_state_json_async()))

    def accessibility_tree(self) -> str:
        """Get the accessibility-tree summary (apps + visible windows) — sync wrapper."""
        return str(self._run_async(self._accessibility_tree_async()))

    def list_windows(self) -> str:
        """Enumerate all top-level windows, including off-screen ones — sync wrapper."""
        return str(self._run_async(self._list_windows_async()))

    def window_state(
        self,
        pid: int,
        window_id: int,
        max_elements: int | None = None,
        max_depth: int | None = None,
        screenshot_out_file: str | None = None,
    ) -> str:
        """Get a window's UI element tree as JSON — sync wrapper."""
        return str(self._run_async(
            self._window_state_async(
                pid, window_id, max_elements, max_depth, screenshot_out_file
            )
        ))

    def hotkey(self, keys: list[str]) -> str:
        """Press a hotkey combination (sync wrapper)."""
        return str(self._run_async(self._hotkey_async(keys)))

    def press_key(self, key: str, modifiers: list[str] | None = None) -> str:
        """Press a single key (sync wrapper)."""
        return str(self._run_async(self._press_key_async(key, modifiers)))

    def clear_field(self) -> str:
        """Select-all then delete, clearing the focused text field."""
        return str(self._run_async(self._clear_field_async()))

    def close(self) -> None:
        """Shut down the CUA driver and its embedded host."""
        if self._driver is not None:
            try:
                from cua_driver import EndSessionInput

                self._run_async(
                    self._driver.end_session(
                        EndSessionInput(session=self._session_name)
                    )
                )
                self._run_async(self._driver.shutdown())
            except Exception:
                pass
            self._driver = None
        if self._host is not None:
            try:
                self._run_async(self._host.stop())
            except Exception:
                pass
            self._host = None
        self._session_started = False


# Module-level singleton for the CUA backend
_instance: CuaComputer | None = None
_lock = threading.Lock()


def get_cua_computer() -> CuaComputer:
    """Get or create the singleton CUA computer instance."""
    global _instance
    with _lock:
        if _instance is None:
            _instance = CuaComputer()
        return _instance


def _close_singleton() -> None:
    """Close the CUA driver before interpreter teardown.

    Dropping the FFI driver reference while the native library is still loaded
    avoids ``CuaDriver.__del__`` firing at shutdown, when the uniffi function
    pointers are already gone (the ``'NoneType' object is not callable`` error).
    """
    global _instance
    inst = _instance
    _instance = None
    if inst is not None:
        try:
            inst.close()
        except Exception:
            pass


atexit.register(_close_singleton)
