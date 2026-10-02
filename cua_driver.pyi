"""Type stubs for cua_driver (external package)."""

from enum import Enum
from typing import Any

def get_binary_path() -> str: ...


class DesktopScope(Enum):
    DESKTOP = "desktop"


class ClickButton(Enum):
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"


class ScrollDirection(Enum):
    DOWN = "down"
    UP = "up"


class ScrollBy(Enum):
    LINE = "line"
    PAGE = "page"


class CaptureScope(Enum):
    AUTO = "auto"
    WINDOW = "window"
    DESKTOP = "desktop"


class CursorReducedMotion(Enum):
    AUTO = "auto"
    ON = "on"
    OFF = "off"


class CursorThemeSelection:
    def __init__(self, *, theme_id: str, reduced_motion: CursorReducedMotion) -> None: ...


class SetAgentCursorEnabledInput:
    def __init__(self, *, session: str, enabled: bool) -> None: ...


class StartSessionInput:
    def __init__(
        self,
        session: str,
        capture_scope: Any = None,
        cursor_theme: Any = None,
    ) -> None: ...


class GetDesktopStateInput:
    def __init__(
        self,
        session: str,
        screenshot_out_file: str | None = None,
    ) -> None: ...


class InputDeliveryMode(Enum):
    BACKGROUND = "background"
    FOREGROUND = "foreground"


class ActionTarget:
    class DESKTOP:
        def __init__(self, display_id: str) -> None: ...
    class WINDOW:
        def __init__(self, pid: int, window_id: int) -> None: ...


class ClickPosition:
    class COORDINATES:
        def __init__(self, x: float, y: float) -> None: ...
    class CAPTURED_COORDINATES:
        def __init__(self, x: float, y: float) -> None: ...
    class ELEMENT:
        def __init__(self, ref: Any) -> None: ...


class ClickInput:
    def __init__(
        self,
        *,
        target: Any,
        position: Any,
        delivery_mode: Any,
        session: str | None = None,
        button: Any = None,
        count: int | None = None,
    ) -> None: ...


class TypeTextInput:
    def __init__(
        self,
        session: str,
        text: str,
        target: Any = None,
        scope: Any = DesktopScope.DESKTOP,
    ) -> None: ...


class ScrollInput:
    def __init__(
        self,
        session: str,
        x: float,
        y: float,
        direction: ScrollDirection = ScrollDirection.DOWN,
        target: Any = None,
        scope: Any = DesktopScope.DESKTOP,
        by: ScrollBy = ScrollBy.LINE,
        amount: int = 3,
    ) -> None: ...


class DragInput:
    def __init__(
        self,
        *,
        from_x: float,
        from_y: float,
        to_x: float,
        to_y: float,
        target: Any = None,
        scope: Any = DesktopScope.DESKTOP,
        session: str | None = None,
        duration_ms: int | None = None,
        steps: int | None = None,
        button: ClickButton | None = None,
        modifier: list[str] | None = None,
    ) -> None: ...


class HotkeyInput:
    def __init__(
        self,
        session: str,
        keys: list[str],
        target: Any = None,
        scope: Any = DesktopScope.DESKTOP,
    ) -> None: ...


class PressKeyInput:
    def __init__(
        self,
        session: str,
        key: str,
        target: Any = None,
        scope: Any = DesktopScope.DESKTOP,
        modifiers: list[str] | None = None,
    ) -> None: ...


class MoveCursorInput:
    def __init__(
        self,
        session: str,
        x: float,
        y: float,
        target: Any = None,
        scope: Any = DesktopScope.DESKTOP,
    ) -> None: ...


class EndSessionInput:
    def __init__(self, session: str) -> None: ...


class CuaDriver:
    @staticmethod
    def create() -> CuaDriver: ...

    @staticmethod
    def connect(socket_path: str | None = None) -> CuaDriver: ...

    async def start_session(self, input: StartSessionInput) -> Any: ...
    async def get_desktop_state(self, input: GetDesktopStateInput) -> Any: ...
    async def call_tool(self, name: str, arguments_json: str) -> Any: ...
    async def click(self, input: ClickInput) -> Any: ...
    async def drag(self, input: DragInput) -> Any: ...
    async def type_text(self, input: TypeTextInput) -> Any: ...
    async def scroll(self, input: ScrollInput) -> Any: ...
    async def hotkey(self, input: HotkeyInput) -> Any: ...
    async def press_key(self, input: PressKeyInput) -> Any: ...
    async def end_session(self, input: EndSessionInput) -> Any: ...
    async def move_cursor(self, input: MoveCursorInput) -> Any: ...
    async def set_agent_cursor_enabled(self, input: SetAgentCursorEnabledInput) -> Any: ...
    async def shutdown(self) -> Any: ...


class EmbeddedDriverHostOptions:
    def __init__(
        self,
        *,
        binary_path: str,
        host_bundle_id: str,
        socket_path: str | None = None,
        startup_timeout_ms: int | None = None,
        shutdown_timeout_ms: int | None = None,
        permission_mode: Any = None,
        capability_manifest_path: str | None = None,
        approve_capability_manifest: bool = False,
        session_policy_path: str | None = None,
        approve_session_policy: bool = False,
        dangerously_bypass_approvals: bool = False,
        environment: list[Any] | None = None,
        inherit_stderr: bool = False,
        no_overlay: bool = False,
    ) -> None: ...


class EmbeddedCuaDriverHost:
    @classmethod
    def with_options(cls, options: EmbeddedDriverHostOptions) -> EmbeddedCuaDriverHost: ...

    async def start(self) -> Any: ...
    async def stop(self) -> None: ...
