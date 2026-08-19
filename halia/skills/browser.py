"""Browser automation skills — Playwright-based browser control.

Provides browser_open, browser_navigate, browser_click, browser_type,
browser_screenshot, browser_read, and browser_close skills.

Phase 1 of the CUA (Computer Use Agent) implementation — the "hands" that
CUA will指挥 in Phase 2.

Trust note: browser actions are potentially dangerous (navigating to unknown
sites, clicking elements, typing). All write actions require approval.
Read-only actions (screenshot, read) are safe.
"""

from __future__ import annotations

import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from halia.computer import display_available

# Browser state — protected by _lock for thread safety
_lock = threading.Lock()
_playwright = None
_browser = None
_context = None
_page = None
_executor = ThreadPoolExecutor(max_workers=1)  # single-thread for Playwright


def _run_in_thread(func: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a function in the Playwright thread to avoid event loop conflicts."""
    future = _executor.submit(func, *args, **kwargs)
    return future.result(timeout=60)


def _ensure_playwright_sync() -> Any:
    """Lazily import and initialize Playwright (sync, in thread)."""
    global _playwright
    if _playwright is None:
        # Check if computer is enabled
        from halia.config.settings import read_config
        config = read_config()
        if not config.get("computer_enabled"):
            raise RuntimeError(
                "halia computer is not enabled. "
                "Run 'halia setup --computer' to enable browser automation."
            )
        try:
            from playwright.sync_api import sync_playwright
            _playwright = sync_playwright().start()
        except ImportError as exc:
            raise RuntimeError(
                "playwright is not installed. Install with: "
                "pip install halia[browser] && playwright install chromium"
            ) from exc
    return _playwright


def _ensure_page_sync(headless: bool | None = None) -> Any:
    """Ensure we have an active browser page (sync, in thread)."""
    global _browser, _context, _page
    if headless is None:
        # Visible by default on a desktop; headless when there is no display.
        headless = not display_available()
    pw = _ensure_playwright_sync()
    if _page is None or _page.is_closed():
        if _browser is None or not _browser.is_connected():
            _close_browser_sync()  # clean up stale state
            _browser = pw.chromium.launch(headless=headless)
            _context = _browser.new_context()
        _page = _context.new_page()
    return _page


def _close_browser_sync() -> None:
    """Close the browser and clean up (sync, in thread)."""
    global _browser, _context, _page
    if _page and not _page.is_closed():
        _page.close()
    _page = None
    if _context:
        _context.close()
    _context = None
    if _browser and _browser.is_connected():
        _browser.close()
    _browser = None


def _ensure_page(headless: bool | None = None) -> Any:
    """Thread-safe wrapper for _ensure_page_sync."""
    with _lock:
        return _run_in_thread(_ensure_page_sync, headless)


def _close_browser() -> None:
    """Thread-safe wrapper for _close_browser_sync."""
    with _lock:
        _run_in_thread(_close_browser_sync)


class BrowserOpen:
    name = "browser_open"
    description = (
        "Open a URL in the browser and return the page content. "
        "Use this to visit a website, read its content, and prepare for "
        "further interaction. Returns the page title and main text content. "
        "Shows a visible browser window by default on desktop; pass "
        "headless=true to run in the background with no window."
    )
    dangerous = True  # navigates to external sites
    untrusted = True  # content from external sites
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "url": {"type": "string", "description": "The URL to open."},
            "headless": {
                "type": "boolean",
                "description": "Run with no visible window. "
                "Default: false on desktop, true on headless servers.",
            },
            "wait": {
                "type": "string",
                "enum": ["load", "domcontentloaded", "networkidle"],
                "description": "Wait condition (default: load).",
            },
        },
        "required": ["url"],
    }

    def run(self, args: dict[str, Any]) -> str:
        url = args.get("url", "")
        if not isinstance(url, str) or not url.strip():
            return "error: 'url' is required"
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        headless_arg = args.get("headless")
        headless = headless_arg if isinstance(headless_arg, bool) else not display_available()

        wait = args.get("wait", "load")
        if wait not in ("load", "domcontentloaded", "networkidle"):
            wait = "load"

        def _open() -> str:
            page = _ensure_page_sync(headless=headless)
            page.goto(url, wait_until=wait)
            title = page.title()
            text = page.evaluate("() => document.body.innerText")
            if len(text) > 10000:
                text = text[:10000] + "... [truncated]"
            mode = "headless" if headless else "headful (visible window)"
            return f"Page: {title}\nURL: {page.url}\nMode: {mode}\n\n{text}"

        try:
            with _lock:
                return _run_in_thread(_open)  # type: ignore[no-any-return]
        except Exception as exc:
            _close_browser()
            return f"error opening {url}: {exc}"


class BrowserNavigate:
    name = "browser_navigate"
    description = (
        "Navigate the current browser page to a new URL. "
        "Requires browser_open to have been called first."
    )
    dangerous = True
    untrusted = True
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "url": {"type": "string", "description": "The URL to navigate to."},
            "wait": {
                "type": "string",
                "enum": ["load", "domcontentloaded", "networkidle"],
                "description": "Wait condition (default: load).",
            },
        },
        "required": ["url"],
    }

    def run(self, args: dict[str, Any]) -> str:
        url = args.get("url", "")
        if not isinstance(url, str) or not url.strip():
            return "error: 'url' is required"
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        wait = args.get("wait", "load")
        if wait not in ("load", "domcontentloaded", "networkidle"):
            wait = "load"

        def _navigate() -> str:
            page = _ensure_page_sync()
            page.goto(url, wait_until=wait)
            title = page.title()
            return f"Navigated to: {title} ({page.url})"

        try:
            with _lock:
                return _run_in_thread(_navigate)  # type: ignore[no-any-return]
        except Exception as exc:
            return f"error navigating to {url}: {exc}"


class BrowserClick:
    name = "browser_click"
    description = (
        "Click an element on the current page. Can click by text content, "
        "CSS selector, or coordinates. Requires browser_open first."
    )
    dangerous = True
    untrusted = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "text": {"type": "string", "description": "Click element containing this text."},
            "selector": {"type": "string", "description": "CSS selector of element to click."},
            "x": {"type": "number", "description": "X coordinate to click."},
            "y": {"type": "number", "description": "Y coordinate to click."},
        },
    }

    def run(self, args: dict[str, Any]) -> str:
        text = args.get("text")
        selector = args.get("selector")
        x = args.get("x")
        y = args.get("y")

        if not any([text, selector, x is not None]):
            return "error: provide 'text', 'selector', or 'x'/'y' coordinates"

        def _click() -> str:
            page = _ensure_page_sync()
            if text:
                page.get_by_text(text, exact=False).first.click()
                return f"Clicked element with text: {text}"
            elif selector:
                page.locator(selector).first.click()
                return f"Clicked element: {selector}"
            elif x is not None and y is not None:
                page.mouse.click(float(x), float(y))
                return f"Clicked at ({x}, {y})"
            return "error: no valid click target"

        try:
            with _lock:
                return _run_in_thread(_click)  # type: ignore[no-any-return]
        except Exception as exc:
            return f"error clicking: {exc}"


class BrowserType:
    name = "browser_type"
    description = (
        "Type text into an input field or text area. Can target by placeholder, "
        "label, selector, or coordinates. Requires browser_open first."
    )
    dangerous = True
    untrusted = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "text": {"type": "string", "description": "Text to type."},
            "placeholder": {"type": "string", "description": "Placeholder text of input field."},
            "label": {"type": "string", "description": "Label text of input field."},
            "selector": {"type": "string", "description": "CSS selector of input field."},
            "press_enter": {
                "type": "boolean",
                "description": "Press Enter after typing (default: false).",
            },
        },
        "required": ["text"],
    }

    def run(self, args: dict[str, Any]) -> str:
        text = args.get("text", "")
        if not text:
            return "error: 'text' is required"

        placeholder = args.get("placeholder")
        label = args.get("label")
        selector = args.get("selector")
        press_enter = args.get("press_enter", False)

        def _type() -> str:
            page = _ensure_page_sync()
            if placeholder:
                page.get_by_placeholder(placeholder).fill(text)
                if press_enter:
                    page.get_by_placeholder(placeholder).press("Enter")
                return f"Typed into placeholder: {placeholder}"
            elif label:
                page.get_by_label(label).fill(text)
                if press_enter:
                    page.get_by_label(label).press("Enter")
                return f"Typed into label: {label}"
            elif selector:
                page.locator(selector).fill(text)
                if press_enter:
                    page.locator(selector).press("Enter")
                return f"Typed into: {selector}"
            else:
                page.keyboard.type(text)
                if press_enter:
                    page.keyboard.press("Enter")
                return f"Typed: {text[:50]}{'...' if len(text) > 50 else ''}"

        try:
            with _lock:
                return _run_in_thread(_type)  # type: ignore[no-any-return]
        except Exception as exc:
            return f"error typing: {exc}"


class BrowserScreenshot:
    name = "browser_screenshot"
    description = (
        "Take a screenshot of the current page. Saves to a file and returns "
        "the path. Automatically checks vision support for the current model."
    )
    dangerous = False
    untrusted = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "path": {
                "type": "string",
                "description": "Output file path (default: temp file or config screenshot_dir).",
            },
            "full_page": {
                "type": "boolean",
                "description": "Capture full scrollable page (default: viewport only).",
            },
            "model": {
                "type": "string",
                "description": "Model name to check vision support (optional).",
            },
        },
    }

    def run(self, args: dict[str, Any]) -> str:
        path = args.get("path")
        full_page = args.get("full_page", False)
        model = args.get("model")

        def _screenshot() -> str:
            page = _ensure_page_sync()
            if path:
                screenshot_path = Path(path).expanduser()
                screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            else:
                # Check config for default screenshot directory
                from halia.config.settings import read_config
                config = read_config()
                screenshot_dir = config.get("screenshot_dir", "")
                if screenshot_dir:
                    dir_path = Path(screenshot_dir).expanduser()
                    dir_path.mkdir(parents=True, exist_ok=True)
                    # Generate filename with timestamp
                    import time
                    ts = time.strftime("%Y%m%d_%H%M%S")
                    screenshot_path = dir_path / f"screenshot_{ts}.png"
                else:
                    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                    screenshot_path = Path(tmp.name)
                    tmp.close()
            page.screenshot(path=str(screenshot_path), full_page=full_page)

            # Build result message
            result = f"Screenshot saved to: {screenshot_path}"

            # Auto-detect model from config if not provided
            nonlocal model
            if not model:
                try:
                    from halia.config.settings import load_config
                    cfg = load_config()
                    model = cfg.model
                except Exception:
                    pass

            # Check vision support if model available
            if model:
                try:
                    from halia.config.settings import load_config
                    from halia.core.agent import build_provider
                    from halia.cua.vision_check import check_vision_support
                    cfg = load_config()
                    provider = build_provider(cfg)
                    has_vision = check_vision_support(model, provider)
                    if has_vision:
                        result += "\nVision: supported — model can analyze images"
                    else:
                        result += "\nVision: NOT supported — model cannot analyze images"
                except Exception as e:
                    result += f"\nVision check failed: {e}"

            return result

        try:
            with _lock:
                return _run_in_thread(_screenshot)  # type: ignore[no-any-return]
        except Exception as exc:
            return f"error taking screenshot: {exc}"


class BrowserRead:
    name = "browser_read"
    description = (
        "Read the current page content. Returns the page title, URL, and "
        "text content. Use to understand what's on the page."
    )
    dangerous = False
    untrusted = True  # content from external sites
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "max_chars": {
                "type": "integer",
                "description": "Max characters to return (default 10000).",
            },
        },
    }

    def run(self, args: dict[str, Any]) -> str:
        max_chars = args.get("max_chars", 10000)
        if not isinstance(max_chars, int) or max_chars <= 0:
            max_chars = 10000

        def _read() -> str:
            page = _ensure_page_sync()
            title = page.title()
            url = page.url
            text = page.evaluate("() => document.body.innerText")
            if len(text) > max_chars:
                text = text[:max_chars] + "... [truncated]"
            return f"Page: {title}\nURL: {url}\n\n{text}"

        try:
            with _lock:
                return _run_in_thread(_read)  # type: ignore[no-any-return]
        except Exception as exc:
            return f"error reading page: {exc}"


class BrowserScroll:
    name = "browser_scroll"
    description = (
        "Scroll the page up or down. Use to reveal content below the fold "
        "or to reach elements that are not currently visible."
    )
    dangerous = False
    untrusted = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "direction": {
                "type": "string",
                "enum": ["up", "down", "top", "bottom"],
                "description": "Scroll direction (default: down).",
            },
            "amount": {
                "type": "integer",
                "description": "Pixels to scroll (default: 500). Ignored for top/bottom.",
            },
        },
    }

    def run(self, args: dict[str, Any]) -> str:
        direction = args.get("direction", "down")
        amount = args.get("amount", 500)

        if direction not in ("up", "down", "top", "bottom"):
            direction = "down"
        if not isinstance(amount, int) or amount <= 0:
            amount = 500

        def _scroll() -> str:
            page = _ensure_page_sync()
            if direction == "top":
                page.evaluate("window.scrollTo(0, 0)")
                return "Scrolled to top"
            elif direction == "bottom":
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                return "Scrolled to bottom"
            elif direction == "up":
                page.evaluate(f"window.scrollBy(0, -{amount})")
                return f"Scrolled up {amount}px"
            else:  # down
                page.evaluate(f"window.scrollBy(0, {amount})")
                return f"Scrolled down {amount}px"

        try:
            with _lock:
                return _run_in_thread(_scroll)  # type: ignore[no-any-return]
        except Exception as exc:
            return f"error scrolling: {exc}"


class BrowserWait:
    name = "browser_wait"
    description = (
        "Wait for an element to appear on the page. Use before clicking or "
        "typing on JS-heavy pages where elements load asynchronously."
    )
    dangerous = False
    untrusted = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "selector": {
                "type": "string",
                "description": "CSS selector to wait for.",
            },
            "text": {
                "type": "string",
                "description": "Wait for text to appear on page.",
            },
            "timeout": {
                "type": "integer",
                "description": "Max wait time in ms (default: 30000).",
            },
        },
    }

    def run(self, args: dict[str, Any]) -> str:
        selector = args.get("selector")
        text = args.get("text")
        timeout = args.get("timeout", 30000)

        if not selector and not text:
            return "error: provide 'selector' or 'text'"
        if not isinstance(timeout, int) or timeout <= 0:
            timeout = 30000

        def _wait() -> str:
            page = _ensure_page_sync()
            if selector:
                page.wait_for_selector(selector, timeout=timeout)
                return f"Element found: {selector}"
            elif text:
                page.wait_for_selector(f"text={text}", timeout=timeout)
                return f"Text found: {text}"
            return "error: no wait target"

        try:
            with _lock:
                return _run_in_thread(_wait)  # type: ignore[no-any-return]
        except Exception as exc:
            return f"error waiting: {exc}"


class BrowserClose:
    name = "browser_close"
    description = (
        "Close the browser and end the session. Always call this when done "
        "with browser automation to free resources."
    )
    dangerous = True
    untrusted = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    }

    def run(self, args: dict[str, Any]) -> str:
        _close_browser()
        return "Browser closed."


class BrowserEnsure:
    name = "browser_ensure"
    description = (
        "Check if browser is running and restart if needed. Use after errors "
        "or when browser may have crashed. Returns current browser status."
    )
    dangerous = False
    untrusted = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "headless": {
                "type": "boolean",
                "description": "Restart with no visible window. "
                "Default: false on desktop, true on headless servers.",
            },
        },
    }

    def run(self, args: dict[str, Any]) -> str:
        headless_arg = args.get("headless")
        headless = headless_arg if isinstance(headless_arg, bool) else not display_available()

        def _ensure() -> str:
            global _browser, _context, _page
            status = []
            # Check if browser is connected
            if _browser is None or not _browser.is_connected():
                status.append("Browser was not running — restarting")
                _close_browser_sync()
                _ensure_page_sync(headless=headless)
            elif _page is None or _page.is_closed():
                status.append("Page was closed — creating new page")
                if _context is None:
                    _ensure_page_sync(headless=headless)
                else:
                    _page = _context.new_page()
            else:
                status.append("Browser is running")
                return f"Browser OK — on page: {_page.url}"

            # If we restarted, report success
            if _page and not _page.is_closed():
                return f"{status[0]}. Now on page: {_page.url}"
            return f"{status[0]}. Restart failed"

        try:
            with _lock:
                return _run_in_thread(_ensure)  # type: ignore[no-any-return]
        except Exception as exc:
            return f"error ensuring browser: {exc}"
