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


class ClickInput:
    def __init__(
        self,
        session: str,
        x: float,
        y: float,
        target: Any = None,
        scope: Any = DesktopScope.DESKTOP,
        button: ClickButton = ClickButton.LEFT,
        count: int = 1,
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

    async def start_session(self, input: StartSessionInput) -> Any: ...
    async def get_desktop_state(self, input: GetDesktopStateInput) -> Any: ...
    async def click(self, input: ClickInput) -> Any: ...
    async def type_text(self, input: TypeTextInput) -> Any: ...
    async def scroll(self, input: ScrollInput) -> Any: ...
    async def hotkey(self, input: HotkeyInput) -> Any: ...
    async def press_key(self, input: PressKeyInput) -> Any: ...
    async def end_session(self, input: EndSessionInput) -> Any: ...
    async def move_cursor(self, input: MoveCursorInput) -> Any: ...
    async def shutdown(self) -> Any: ...
