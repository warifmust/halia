"""A REPL-style TUI chat shell for halia (prompt_toolkit + rich) — starting small.

This first slice is the INPUT experience, which a naive `input()` gets wrong:
  * multi-line PASTE — bracketed paste drops the whole block in as one input instead
    of splitting each line into a separate submit;
  * Option/Alt+Arrow — proper word-by-word cursor movement instead of leaking escape
    sequences (the "jargon characters") into the line.
On submit, the (possibly multi-line) text is echoed back. Wiring it to the model comes
next; here we just prove the input feels right.
"""

from __future__ import annotations

from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings
from rich.console import Console
from rich.text import Text

# Block-letter "HALIA" (ANSI-Shadow style).
HALIA_BANNER = r"""
██   ██  █████  ██      ██  █████
██   ██ ██   ██ ██      ██ ██   ██
███████ ███████ ██      ██ ███████
██   ██ ██   ██ ██      ██ ██   ██
██   ██ ██   ██ ███████ ██ ██   ██
"""

_console = Console()
PROMPT = "› "


def render_banner(console: Console | None = None) -> None:
    """Print the HALIA banner + a one-line hint."""
    con = console or _console
    con.print(Text(HALIA_BANNER, style="bold yellow"))
    con.print("[dim]trust-first agent · paste multi-line freely · /exit to quit[/dim]\n")


def build_key_bindings() -> KeyBindings:
    """Word-wise cursor movement for Option/Alt+Arrow and Ctrl+Arrow."""
    kb = KeyBindings()

    def back_word(event: Any) -> None:
        buff = event.current_buffer
        buff.cursor_position += buff.document.find_previous_word_beginning(count=1) or 0

    def forward_word(event: Any) -> None:
        buff = event.current_buffer
        buff.cursor_position += buff.document.find_next_word_ending(count=1) or 0

    # macOS Option+Left / Option+Right typically arrive as Esc + arrow.
    kb.add("escape", "left")(back_word)
    kb.add("escape", "right")(forward_word)
    # Many terminals emit Ctrl+Left / Ctrl+Right for word jumps.
    kb.add("c-left")(back_word)
    kb.add("c-right")(forward_word)
    return kb


def build_session(**kwargs: Any) -> PromptSession[str]:
    """A prompt session with our key bindings; bracketed paste is on by default.

    `multiline=False` keeps Enter as submit, while a bracketed paste of several lines
    still lands in the buffer as one multi-line input.
    """
    return PromptSession(key_bindings=build_key_bindings(), multiline=False, **kwargs)


def run_tui() -> None:
    """Banner + input loop that echoes each submission (multi-line paste shown back)."""
    render_banner()
    session = build_session()
    while True:
        try:
            text = session.prompt(PROMPT)
        except (EOFError, KeyboardInterrupt):
            _console.print("[dim]bye.[/dim]")
            break
        text = text.strip()
        if not text:
            continue
        if text.lower() in ("/exit", "/quit"):
            _console.print("[dim]bye.[/dim]")
            break
        lines = text.splitlines()
        label = "you submitted" if len(lines) == 1 else f"you submitted ({len(lines)} lines)"
        _console.print(f"[cyan]{label}:[/cyan]")
        for line in lines:
            _console.print(f"  {line}")
        _console.print()
