"""Computer backends — abstract interface for desktop automation.

Supports two backends:
- "halia" — Playwright-based browser automation (default)
- "cua" — cua-driver desktop automation
"""

import os
import sys


def display_available() -> bool:
    """Whether a graphical display is available for a visible browser window.

    macOS and Windows always have a window server; Linux needs ``DISPLAY``
    (X11) or ``WAYLAND_DISPLAY`` set in halia's environment. Headless servers
    (e.g. Proxmox) return False.
    """
    if sys.platform in ("win32", "darwin"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
