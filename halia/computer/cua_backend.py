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
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any


def cua_available() -> bool:
    """Whether CUA desktop automation can run in this environment.

    CUA drives a real desktop through the native window server. On headless
    Linux there is no display, so the driver cannot load its X11 libraries or
    open windows — return False so callers can fall back instead of surfacing
    cryptic `libXi.so.6` / `xdg-open` failures.
    """
    from halia.computer import display_available
    return display_available()


class CuaComputer:
    """Desktop automation via cua-driver SDK."""

    def __init__(self) -> None:
        self._driver: Any = None
        self._session_name = "halia"
        self._lock = threading.Lock()
        self._session_started = False

    def _ensure_driver(self) -> Any:
        """Lazy-init the cua-driver."""
        if self._driver is not None:
            return self._driver

        try:
            from cua_driver import CuaDriver
        except ImportError as exc:
            raise RuntimeError(
                "cua-driver is not installed. "
                "Run `halia setup --cua` to install it."
            ) from exc

        self._driver = CuaDriver.create()
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

    async def _ensure_session(self) -> Any:
        """Ensure we have an active CUA session (started once, then reused)."""
        driver = self._ensure_driver()
        if self._session_started:
            return driver
        from cua_driver import StartSessionInput

        await driver.start_session(
            StartSessionInput(
                session=self._session_name,
                capture_scope=None,
                cursor_theme=None,
            )
        )
        self._session_started = True
        return driver

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
                if not self._is_session_ended(str(exc)):
                    raise
                # Session ended — drop the stale flag so the next attempt restarts.
                self._session_started = False
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

    async def _click_async(
        self, x: float, y: float, button: str = "left", count: int = 1
    ) -> str:
        """Click at coordinates via cua-driver (count=2 for a double-click)."""
        from cua_driver import ClickButton, ClickInput, DesktopScope

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
                    session=self._session_name,
                    x=x,
                    y=y,
                    target=None,
                    scope=DesktopScope.DESKTOP,
                    button=btn,
                    count=count,
                )
            )

        await self._with_session_retry(_op)
        verb = "Double-clicked" if count >= 2 else "Clicked"
        return f"{verb} {button} at ({x}, {y})"

    async def _type_async(self, text: str) -> str:
        """Type text via cua-driver."""
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
        return f"Typed: {text[:50]}{'...' if len(text) > 50 else ''}"

    async def _scroll_async(
        self, x: float, y: float, direction: str = "down", amount: int = 3
    ) -> str:
        """Scroll at coordinates via cua-driver."""
        from cua_driver import DesktopScope, ScrollBy, ScrollDirection, ScrollInput

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

    def click(self, x: float, y: float, button: str = "left") -> str:
        """Click at coordinates (sync wrapper)."""
        return str(self._run_async(self._click_async(x, y, button)))

    def double_click(self, x: float, y: float, button: str = "left") -> str:
        """Double-click at coordinates (sync wrapper)."""
        return str(self._run_async(self._click_async(x, y, button, count=2)))

    def type_text(self, text: str) -> str:
        """Type text (sync wrapper)."""
        return str(self._run_async(self._type_async(text)))

    def scroll(self, x: float, y: float, direction: str = "down", amount: int = 3) -> str:
        """Scroll at coordinates (sync wrapper)."""
        return str(self._run_async(self._scroll_async(x, y, direction, amount)))

    def desktop_state(self) -> str:
        """Get desktop state (sync wrapper)."""
        return str(self._run_async(self._desktop_state_async()))

    def desktop_state_json(self) -> str:
        """Get the raw desktop-state JSON (element tree) — sync wrapper."""
        return str(self._run_async(self._desktop_state_json_async()))

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
        """Shut down the CUA driver."""
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
