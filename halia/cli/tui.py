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

import sys
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.input import ansi_escape_sequences as _aes
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from rich.console import Console
from rich.text import Text

from halia.providers.base import Message

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
        "[dim]trust-first agent · Enter to send · Option+Enter for a newline · "
        "/procedure · /iters · /local · /resume · /clear · /exit[/dim]\n"
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


def run_tui(
    profile: str | None = None,
    allow_commands: bool = False,
    resume: str | None = None,
    max_iters: int = 30,
    allow_local: bool = False,
) -> None:
    """Banner + a real chat loop: the prompt_toolkit input feeds the converse() loop.

    Conversations persist to SQLite (like `halia chat`) and survive a restart; pass
    `resume` to continue a saved session. Slash commands (/procedure, /resume, /clear)
    mirror the chat command.
    """
    from dataclasses import replace

    # Imported lazily — cli.main imports this module for the `tui` command (avoid a cycle).
    from halia.audit.record import new_record, save_run
    from halia.cli.main import (
        _chat_procedure,
        _chat_resume,
        _make_approver,
        _prepare_context,
        _resumed_age_note,
        _show_step,
        console,
    )
    from halia.core.agent import SYSTEM_PROMPT, RunLimitError, converse
    from halia.core.session import get_session, new_session, save_session
    from halia.permissions.network import set_allow_local
    from halia.providers.base import ProviderError

    set_allow_local(allow_local)
    render_banner()

    if resume is not None:
        sess = get_session(resume)
        if sess is None:
            console.print(f"[yellow]no session '{resume}'[/yellow] — see `halia sessions`.")
            return
        config, registry, _ = _prepare_context(sess.profile, sess.allow_commands)
        config = replace(config, model=sess.model)
        messages: list[Message] = list(sess.messages)
        console.print(
            f"[dim]resumed session [bold]{sess.id}[/bold] — {sess.turn_count()} turns, "
            f"last active {_resumed_age_note(sess.updated_at)}[/dim]\n"
        )
    else:
        config, registry, extra_system = _prepare_context(profile, allow_commands)
        messages = [{"role": "system", "content": SYSTEM_PROMPT + extra_system}]
        sess = new_session(config.provider, config.model, profile, allow_commands, messages)
        save_session(sess)

    def persist() -> None:
        save_session(replace(sess, messages=list(messages)))

    def farewell() -> None:
        # The resume hint belongs here, on the way OUT — you only resume after closing.
        console.print(
            f"[dim]session {sess.id} — resume with `halia tui --resume {sess.id}`[/dim]"
        )
        console.print("[dim]bye.[/dim]")

    approve = _make_approver()  # one trust scope for the whole session
    session = build_session()
    budget = max_iters  # tool-call rounds per turn; raise it live with /iters
    local_note = " · local egress ON" if allow_local else ""
    console.print(
        f"[dim]tool-call budget: {budget}/turn (raise with /iters N){local_note}[/dim]\n"
    )

    while True:
        try:
            user_input = session.prompt(PROMPT).strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            farewell()
            break
        if not user_input:
            continue
        if user_input.lower() in ("/exit", "/quit"):
            farewell()
            break
        if user_input.lower() == "/clear":
            del messages[1:]  # keep the system prompt
            persist()
            console.print("[dim]context cleared.[/dim]\n")
            continue
        if user_input.lower().startswith("/local"):
            from halia.permissions.network import allow_local_enabled, set_allow_local

            parts = user_input.split()
            if len(parts) >= 2 and parts[1].lower() in ("on", "off"):
                set_allow_local(parts[1].lower() == "on")
            else:
                set_allow_local(not allow_local_enabled())  # bare /local toggles
            state = "ON" if allow_local_enabled() else "OFF"
            console.print(
                f"[dim]local egress {state} — http_request "
                f"{'can' if allow_local_enabled() else 'cannot'} reach localhost/LAN.[/dim]\n"
            )
            continue
        if user_input.lower().startswith("/iters"):
            parts = user_input.split()
            if len(parts) >= 2 and parts[1].isdigit() and int(parts[1]) > 0:
                budget = int(parts[1])
                console.print(f"[dim]tool-call budget set to {budget}/turn.[/dim]\n")
            else:
                console.print(f"[dim]tool-call budget is {budget}/turn. Usage: /iters N[/dim]\n")
            continue
        if user_input.lower().startswith("/resume"):
            _chat_resume(user_input, config, registry)
            continue
        if user_input.lower().startswith("/procedure"):
            to_run = _chat_procedure(user_input)
            if to_run is None:
                continue
            user_input = to_run  # a `/procedure run` — fall through to execute it

        messages.append({"role": "user", "content": user_input})
        # Stream the answer token-by-token; print the "halia ›" header on the first delta
        # (so it lands after any tool-call trace, not before).
        streamed = False

        def on_delta(token: str) -> None:
            nonlocal streamed
            if not streamed:
                console.print("[bold]halia ›[/bold] ", end="")
                streamed = True
            sys.stdout.write(token)
            sys.stdout.flush()

        try:
            result = converse(
                messages, config, registry, max_iters=budget,
                observer=_show_step, approver=approve, on_delta=on_delta,
            )
        except (ProviderError, RunLimitError) as exc:
            if streamed:
                sys.stdout.write("\n")
            console.print(f"[red]error:[/red] {exc}")
            if isinstance(exc, RunLimitError):
                console.print(
                    f"[dim]raise the budget with /iters {budget * 2} and ask again, "
                    "or narrow the task.[/dim]"
                )
            console.print()
            messages.pop()  # drop the failed turn so history stays clean
            continue
        messages.append({"role": "assistant", "content": result.answer})
        persist()  # conversation survives a restart from here
        if streamed:
            sys.stdout.write("\n")
            sys.stdout.flush()
        else:
            # No text was streamed (e.g. a tool-only turn that ended empty) — print it whole.
            console.print(f"[bold]halia ›[/bold] {result.answer}")
        if result.unverified:
            figures = ", ".join(result.unverified)
            console.print(f"[yellow]⚠ unverified figures:[/yellow] {figures}")
        console.print()

        record = new_record(
            config.provider, config.model, user_input, result.answer, result.steps,
            unverified=result.unverified, corrections=result.corrections,
        )
        save_run(record)
