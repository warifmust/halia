"""Browser automation skills — Playwright-based browser control.

Provides browser_open, browser_navigate, browser_new_tab, browser_switch_tab,
browser_click, browser_type, browser_screenshot, browser_read, browser_extract,
and browser_close skills.

Phase 1 of the CUA (Computer Use Agent) implementation — the "hands" that
CUA will指挥 in Phase 2.

Trust note: browser automation is gated by a one-time "full browser control"
consent prompt (like CUA). After consent, all browser actions — navigate, read,
extract, click, type, tabs — run freely. Destructive actions (shell commands)
are still gated per-action by the existing approval floor.
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


def _chrome_installed() -> bool:
    """Best-effort detection of a system Google Chrome install."""
    import shutil
    import sys

    if sys.platform == "darwin":
        return Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome").exists()
    if sys.platform == "win32":
        import os

        candidates = [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        ]
        return any(Path(c).exists() for c in candidates)
    return (
        shutil.which("google-chrome") is not None
        or shutil.which("google-chrome-stable") is not None
    )


def _browser_launch_kwargs() -> dict[str, Any]:
    """Resolve which Chromium binary to launch, preferring a real browser.

    Default ("auto"): the system Google Chrome if installed (full codecs, DRM,
    a real browser) — else Playwright's bundled full Chromium. `channel="chromium"`
    selects the full build rather than the stripped headless shell. Override with
    config `browser_channel` (chrome/msedge/chromium) or `browser_executable_path`
    (Arc, Brave, …).
    """
    from halia.config.settings import read_config

    config = read_config()
    exe = config.get("browser_executable_path", "")
    if isinstance(exe, str) and exe.strip():
        return {"executable_path": exe.strip()}
    channel = config.get("browser_channel", "auto")
    if channel in ("chrome", "msedge", "chromium"):
        return {"channel": channel}
    if _chrome_installed():
        return {"channel": "chrome"}
    return {"channel": "chromium"}


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
            launch_kwargs = _browser_launch_kwargs()
            launch_kwargs["headless"] = headless
            _browser = pw.chromium.launch(**launch_kwargs)
            _context = _browser.new_context()
        # If other tabs are still open, adopt the most recent one; otherwise
        # open a fresh page. This keeps multi-tab sessions working after the
        # active tab is closed.
        pages = _context.pages
        _page = pages[-1] if pages else _context.new_page()
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
    dangerous = False  # covered by the one-time browser consent gate
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
    dangerous = False
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


class BrowserNewTab:
    name = "browser_new_tab"
    description = (
        "Open a URL in a new browser tab and switch to it. Keeps the previous "
        "page open in the background. Use browser_switch_tab to move between "
        "tabs. Requires browser_open first (opens one if not already open)."
    )
    dangerous = False  # covered by the one-time browser consent gate
    untrusted = True  # content from external sites
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "url": {"type": "string", "description": "The URL to open in the new tab."},
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

        def _new_tab() -> str:
            global _page
            had_page = _page is not None and not _page.is_closed()
            page = _ensure_page_sync()
            if had_page:
                new_page = page.context.new_page()
                _page = new_page
            else:
                # First tab — reuse the blank page we just ensured.
                new_page = page
            new_page.goto(url, wait_until=wait)
            title = new_page.title()
            text = new_page.evaluate("() => document.body.innerText")
            if len(text) > 10000:
                text = text[:10000] + "... [truncated]"
            count = len(page.context.pages)
            return (
                f"Opened new tab ({count} open): {title}\n"
                f"URL: {new_page.url}\n\n{text}"
            )

        try:
            with _lock:
                return _run_in_thread(_new_tab)  # type: ignore[no-any-return]
        except Exception as exc:
            return f"error opening new tab {url}: {exc}"


class BrowserSwitchTab:
    name = "browser_switch_tab"
    description = (
        "Manage browser tabs. With no arguments, lists all open tabs with their "
        "index, title, and URL. Pass 'index' to switch to that tab, or 'close' "
        "to close the tab at that index. Other browser tools act on the active "
        "tab."
    )
    dangerous = False
    untrusted = True  # lists titles/URLs from external sites
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "index": {
                "type": "integer",
                "description": "Switch to the tab at this 0-based index.",
            },
            "close": {
                "type": "integer",
                "description": "Close the tab at this 0-based index.",
            },
        },
    }

    def run(self, args: dict[str, Any]) -> str:
        index = args.get("index")
        close = args.get("close")

        def _switch() -> str:
            global _page
            page = _ensure_page_sync()
            pages = list(page.context.pages)

            if close is not None:
                if not isinstance(close, int) or close < 0 or close >= len(pages):
                    return f"error: no tab at index {close} ({len(pages)} tab(s) open)"
                pages[close].close()
                remaining = list(page.context.pages)
                _page = remaining[-1] if remaining else None
                return f"Closed tab {close}. {len(remaining)} tab(s) open."

            if index is not None:
                if not isinstance(index, int) or index < 0 or index >= len(pages):
                    return f"error: no tab at index {index} ({len(pages)} tab(s) open)"
                _page = pages[index]
                _page.bring_to_front()
                return f"Switched to tab {index}: {_page.title()} ({_page.url})"

            if not pages:
                return "No tabs open."
            lines = []
            for i, p in enumerate(pages):
                marker = " *" if p is _page else ""
                title = p.title()[:60]
                lines.append(f"{i}{marker}: {title} — {p.url}")
            return "Tabs:\n" + "\n".join(lines)

        try:
            with _lock:
                return _run_in_thread(_switch)  # type: ignore[no-any-return]
        except Exception as exc:
            return f"error managing tabs: {exc}"


class BrowserClick:
    name = "browser_click"
    description = (
        "Click an element on the current page. Can click by text content, "
        "CSS selector, or coordinates. Requires browser_open first."
    )
    dangerous = False  # covered by the one-time browser consent gate
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
    dangerous = False  # covered by the one-time browser consent gate
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
        "the page as an image the model can analyze visually. Also checks "
        "vision support for the current model."
    )
    dangerous = False
    untrusted = False
    # Multi-modal: the tool result includes an image content block.
    multi_modal = True
    # Side-channel: agent loop reads this after the tool runs.
    _pending_image: str | None = None
    _pending_detail: str | None = None
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

            # Encode the image and stage it for the agent loop to inject as a
            # visual observation (same side-channel as cua_screenshot). Keep it
            # reasonably sized so image tokens don't blow up the context window.
            try:
                import base64
                import io

                from PIL import Image

                img: Image.Image = Image.open(screenshot_path)
                img.thumbnail((1600, 4096), Image.Resampling.LANCZOS)
                if img.mode != "RGB":
                    img = img.convert("RGB")
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=90, optimize=True)
                BrowserScreenshot._pending_image = base64.b64encode(
                    buf.getvalue()
                ).decode("ascii")
                BrowserScreenshot._pending_detail = "high"
                dims = f" ({img.size[0]}x{img.size[1]})"
            except Exception:
                import base64

                BrowserScreenshot._pending_image = base64.b64encode(
                    screenshot_path.read_bytes()
                ).decode("ascii")
                BrowserScreenshot._pending_detail = "high"
                dims = ""

            # Build result message
            result = (
                f"Screenshot saved to: {screenshot_path}{dims}. "
                "Analyze the attached image."
            )

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


class BrowserExtract:
    name = "browser_extract"
    description = (
        "Extract structured data from the current page by CSS selector. "
        "Returns the tag, text, and an optional attribute value for each "
        "matching element. Use to pull links, table cells, or form values."
    )
    dangerous = False
    untrusted = True  # content from external sites
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "selector": {
                "type": "string",
                "description": "CSS selector to query (e.g. 'a', '.price', '#result').",
            },
            "attribute": {
                "type": "string",
                "description": "Optional: return this attribute's value "
                "(e.g. 'href', 'src', 'value'). Omit to return text content.",
            },
            "all": {
                "type": "boolean",
                "description": "Extract all matches (default: false — first match only).",
            },
            "max_items": {
                "type": "integer",
                "description": "Max elements to return when all=true (default: 50).",
            },
        },
        "required": ["selector"],
    }

    def run(self, args: dict[str, Any]) -> str:
        selector = args.get("selector", "")
        if not isinstance(selector, str) or not selector.strip():
            return "error: 'selector' is required"
        attribute = args.get("attribute") or ""
        all_matches = args.get("all", False)
        max_items = args.get("max_items", 50)
        if not isinstance(max_items, int) or max_items <= 0:
            max_items = 50
        limit = max_items if all_matches else 1

        def _extract() -> str:
            import json

            page = _ensure_page_sync()
            js = """(opts) => {
                const els = Array.from(document.querySelectorAll(opts.selector))
                    .slice(0, opts.limit);
                return els.map((el, index) => {
                    const out = { index, tag: el.tagName.toLowerCase() };
                    if (opts.attribute) {
                        out[opts.attribute] = el.getAttribute(opts.attribute);
                    } else {
                        const text = (el.innerText || el.textContent || '')
                            .replace(/\\s+/g, ' ').trim();
                        out.text = text.length > 500 ? text.slice(0, 500) + '…' : text;
                    }
                    return out;
                });
            }"""
            items = page.evaluate(
                js, {"selector": selector, "attribute": attribute, "limit": limit}
            )
            if not items:
                return f"No elements matched: {selector}"
            return (
                f"{len(items)} element(s) matched {selector}:\n"
                + json.dumps(items, ensure_ascii=False, indent=2)
            )

        try:
            with _lock:
                return _run_in_thread(_extract)  # type: ignore[no-any-return]
        except Exception as exc:
            return f"error extracting: {exc}"


class BrowserClose:
    name = "browser_close"
    description = (
        "Close the browser and end the session. Always call this when done "
        "with browser automation to free resources."
    )
    dangerous = False
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
