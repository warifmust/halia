"""Shared input helpers — prompt_toolkit everywhere so arrow keys, editing,
and paste work correctly in any terminal (VS Code, macOS Terminal, etc.).
"""

from __future__ import annotations

import sys

from prompt_toolkit import prompt as pt_prompt
from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style

_STYLE = Style.from_dict({"prompt": "bold ansigreen"})

# Picker style — dimmed hint only; selected option text is colored inline.
_PICK_STYLE = Style.from_dict({
    "hint": "dim",
})

# Radio-button bullets.
_SELECTED = "◉"
_UNSELECTED = "○"


def ask(prompt_text: str = "❯ ", default: str = "", is_password: bool = False) -> str:
    """Get a line of user input with proper arrow-key / editing support."""
    return pt_prompt(
        [("class:prompt", prompt_text)],
        default=default,
        is_password=is_password,
        style=_STYLE,
    )


def pick(title: str, options: list[str], default: int = 0) -> str:
    """Show a radio-button list navigable with ↑/↓, select with Enter.

    Falls back to numbered list + text input when there's no TTY.
    """
    if not options:
        return ""
    idx = max(0, min(default, len(options) - 1))

    if not sys.stdin.isatty():
        return _pick_fallback(title, options, idx)

    return _pick_pt(title, options, idx)


def _pick_fallback(title: str, options: list[str], default: int) -> str:
    """Numbered-list fallback when there's no TTY."""
    print(title)
    for i, opt in enumerate(options):
        mark = "→" if i == default else " "
        print(f"  {mark} [{i + 1}] {opt}")
    choice = ask(f"\nPick [1-{len(options)}] (default {default + 1}): ", default=str(default + 1))
    try:
        return options[int(choice) - 1]
    except (ValueError, IndexError):
        return options[default]


def _pick_pt(title: str, options: list[str], initial: int) -> str:
    """Radio-button picker using prompt_toolkit for reliable key handling."""
    idx = initial
    n = len(options)
    result: list[str] = []  # mutable container so the handler can set it

    def _text() -> list[tuple[str, str]]:
        lines: list[tuple[str, str]] = [("", title), ("", "\n")]
        for i, opt in enumerate(options):
            style = "ansigreen" if i == idx else ""
            lines.append((style, f"  {_SELECTED if i == idx else _UNSELECTED} {opt}"))
            lines.append(("", "\n"))
        lines.append(("", "\n"))
        lines.append(("class:hint", "  ↑/↓ navigate  ↵ select  esc cancel"))
        return lines

    kb = KeyBindings()

    @kb.add("up")
    def _up(event: KeyPressEvent) -> None:
        nonlocal idx
        if idx > 0:
            idx -= 1

    @kb.add("down")
    def _down(event: KeyPressEvent) -> None:
        nonlocal idx
        if idx < n - 1:
            idx += 1

    @kb.add("enter")
    def _enter(event: KeyPressEvent) -> None:
        result.append(options[idx])
        event.app.exit()

    @kb.add("escape")
    def _escape(event: KeyPressEvent) -> None:
        result.append(options[initial])
        event.app.exit()

    @kb.add("c-c")
    @kb.add("c-d")
    def _cancel(event: KeyPressEvent) -> None:
        result.append(options[initial])
        event.app.exit()

    layout = Layout(
        HSplit([Window(FormattedTextControl(_text), dont_extend_height=True)])  # type: ignore[arg-type]
    )

    app: Application[None] = Application(
        layout=layout,
        key_bindings=kb,
        style=_PICK_STYLE,
        full_screen=False,
        erase_when_done=False,
    )
    app.run()

    sys.stdout.write("\n")
    return result[0] if result else options[initial]
