"""Computer backends — abstract interface for desktop automation.

CUA (cua-driver desktop automation) is halia's only computer backend.
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
