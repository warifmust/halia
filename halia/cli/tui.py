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
from prompt_toolkit.input import ansi_escape_sequences as _aes
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from rich.console import Console
from rich.text import Text

# Shift+Enter has no universal byte sequence — most terminals send the SAME code as
# plain Enter, so it can't be told apart. Terminals that DO emit a distinct sequence
# use one of these (CSI-u or xterm modifyOtherKeys); by default prompt_toolkit folds
# them into ControlM (submit). Remap them to ControlJ so they insert a newline instead.
# On terminals that send plain Enter for Shift+Enter, use Option+Enter (below).
for _seq in ("\x1b[13;2u", "\x1b[27;2;13~"):
    _aes.ANSI_SEQUENCES[_seq] = Keys.ControlJ

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
    con.print(
        "[dim]trust-first agent · Enter to send · Shift/Option+Enter for a newline · "
        "/exit to quit[/dim]\n"
    )


def build_key_bindings() -> KeyBindings:
    """Enter submits; Option/Shift+Enter and Ctrl+J insert a newline; Alt/Ctrl+Arrow word-jump."""
    kb = KeyBindings()

    def back_word(event: Any) -> None:
        buff = event.current_buffer
        buff.cursor_position += buff.document.find_previous_word_beginning(count=1) or 0

    def forward_word(event: Any) -> None:
        buff = event.current_buffer
        buff.cursor_position += buff.document.find_next_word_ending(count=1) or 0

    def submit(event: Any) -> None:
        event.current_buffer.validate_and_handle()

    def newline(event: Any) -> None:
        event.current_buffer.insert_text("\n")

    # In multiline mode, plain Enter would insert a newline — flip it so Enter SUBMITS.
    kb.add("c-m")(submit)
    # Newline without submitting: Option/Alt+Enter (reliable everywhere) + Ctrl+J
    # (which the remapped Shift+Enter sequences also resolve to).
    kb.add("escape", "c-m")(newline)
    kb.add("c-j")(newline)

    # macOS Option+Left / Option+Right typically arrive as Esc + arrow.
    kb.add("escape", "left")(back_word)
    kb.add("escape", "right")(forward_word)
    # Many terminals emit Ctrl+Left / Ctrl+Right for word jumps.
    kb.add("c-left")(back_word)
    kb.add("c-right")(forward_word)
    return kb


def build_session(**kwargs: Any) -> PromptSession[str]:
    """A prompt session with our key bindings; bracketed paste is on by default.

    `multiline=True` so an inserted newline edits properly (up/down between lines);
    our `c-m` binding keeps Enter as submit. A bracketed paste of several lines still
    lands in the buffer as one multi-line input.
    """
    return PromptSession(key_bindings=build_key_bindings(), multiline=True, **kwargs)


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
