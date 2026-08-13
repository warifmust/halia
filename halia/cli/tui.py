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

import threading
import time
from collections.abc import Iterable
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.input import ansi_escape_sequences as _aes
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from rich.console import Console
from rich.markup import escape
from rich.text import Text

from halia.providers.base import Message, Usage

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
PROMPT = HTML("<b><ansigreen>❯</ansigreen></b> ")  # bold green chevron — clear "your turn"

# Slash commands: (command, description). Drives both /help and the completion dropdown.
_SLASH_COMMANDS: list[tuple[str, str]] = [
    ("/help", "show all commands"),
    ("/history", "show the last n turns (default 10)"),
    ("/cost", "session token usage (+ % cached, rough $ estimate)"),
    ("/token", "show/hide token usage in the status bar (on/off)"),
    ("/export", "save the conversation as markdown (optional path)"),
    ("/model", "show or switch the model (name)"),
    ("/profile", "show or switch the profile (name)"),
    ("/undo", "drop the last exchange (conversation only)"),
    ("/teach", "store a file or URL as a reference (path/URL, --profile)"),
    ("/files", "list or search taught reference files"),
    ("/image", "attach an image for vision analysis (path to file)"),
    ("/procedure", "manage test procedures (list/teach/show/set/run/remove)"),
    ("/iters", "set the tool-call budget per turn"),
    ("/local", "let http_request reach localhost/LAN"),
    ("/commands", "enable shell commands (run_command; approval-gated)"),
    ("/resume", "resume a paused run"),
    ("/compact", "summarise older turns to free up context"),
    ("/clear", "reset the conversation (keeps the persona)"),
    ("/exit", "quit halia"),
    ("/quit", "quit halia (alias of /exit)"),
]
_SLASH_HELP = "[bold]slash commands[/bold]\n" + "\n".join(
    f"  [cyan]{cmd}[/cyan]  [dim]{desc}[/dim]" for cmd, desc in _SLASH_COMMANDS
)


class _SlashCompleter(Completer):
    """Dropdown completion for slash commands: `/` shows all, `/q` filters to matches."""

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        text = document.text_before_cursor
        # Only for a leading slash command, and only while still typing the command itself.
        if not text.startswith("/") or " " in text:
            return
        for cmd, desc in _SLASH_COMMANDS:
            if cmd.startswith(text.lower()):
                yield Completion(
                    cmd, start_position=-len(text), display=cmd, display_meta=desc
                )

_BRAILLE = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

# What halia is doing right now → (emoji, message). Keyed by tool name; "" = thinking.
_ACTIVITY: dict[str, tuple[str, str]] = {
    "": ("🌱", "Growing an answer"),
    "read_file": ("📖", "Reading"),
    "read_pdf": ("📖", "Reading"),
    "read_docx": ("📖", "Reading"),
    "read_csv": ("📖", "Reading"),
    "read_excel": ("📖", "Reading"),
    "list_files": ("🗂️", "Looking around"),
    "http_request": ("☕️", "Calling the endpoint"),
    "fetch_url": ("🔎", "Reading the web"),
    "web_search": ("🔎", "Searching the web"),
    "check_expectation": ("✅", "Checking the verdict"),
    "check_qa_artifact": ("✅", "Checking completeness"),
    "check_requirements": ("✅", "Checking coverage"),
    "calculate": ("🧮", "Crunching numbers"),
    "aggregate_csv": ("🧮", "Crunching numbers"),
    "group_by": ("🧮", "Crunching numbers"),
    "reconcile_csv": ("🧮", "Reconciling"),
    "query_data": ("🧮", "Querying the data"),
    "query_db": ("🧮", "Querying the data"),
    "clean_csv": ("🧹", "Cleaning the data"),
    "readability": ("📖", "Checking readability"),
    "count_text": ("🔤", "Counting"),
    "make_chart": ("📊", "Drawing a chart"),
    "write_file": ("✍️", "Writing it up"),
    "make_pdf": ("✍️", "Writing it up"),
    "make_docx": ("✍️", "Writing it up"),
    "make_excel": ("✍️", "Writing it up"),
    "make_pptx": ("✍️", "Writing it up"),
    "save_procedure": ("💾", "Remembering it"),
    "run_command": ("⚙️", "Running a command"),
    "compacting": ("🗜", "Compacting memory"),
}
_DEFAULT_ACTIVITY = ("🔧", "Working")
_MIN_ACTIVITY_HOLD = 0.7  # seconds to keep a tool's footer message up (fast tools flash by)


class _Footer:
    """A live 'working' line — spinner · emoji message · elapsed · tool count — during a turn.

    Backed by a rich Live so tool-trace lines print cleanly above it; `set_activity` (fed by
    the agent's on_activity hook) swaps the message to what halia is doing right now.
    """

    def __init__(self, console: Console) -> None:
        self._console = console
        self._start = 0.0
        self._tools = 0
        self._activity = _ACTIVITY[""]
        self._hold_until = 0.0  # keep a tool's message up at least this long (so it's seen)
        self._pending_think = False
        self._live: Any = None
        self._ticker: threading.Thread | None = None
        self._stop_tick = threading.Event()

    def reset(self) -> None:
        self._start = time.perf_counter()
        self._tools = 0
        self._activity = _ACTIVITY[""]
        self._hold_until = 0.0
        self._pending_think = False

    def start(self) -> None:
        if self._live is None:
            from rich.live import Live

            # auto_refresh off — we drive the redraw ourselves so the spinner reliably
            # animates in every terminal (rich's own timer can stall).
            self._live = Live(
                self, console=self._console, transient=True, auto_refresh=False
            )
            self._live.start()
            self._stop_tick.clear()
            self._ticker = threading.Thread(target=self._tick, daemon=True)
            self._ticker.start()

    def _tick(self) -> None:
        while not self._stop_tick.is_set():
            live = self._live
            if live is None:
                break
            try:
                live.refresh()
            except Exception:  # noqa: BLE001 — terminal race on shutdown; just stop ticking
                break
            self._stop_tick.wait(0.08)  # ~12 fps

    def stop(self) -> None:
        if self._live is not None:
            self._stop_tick.set()
            if self._ticker is not None:
                self._ticker.join(timeout=0.3)
                self._ticker = None
            self._live.stop()
            self._live = None

    def set_activity(self, label: str) -> None:
        if label:  # a tool is starting — show it, and hold it briefly so fast tools are seen
            self._tools += 1
            self._activity = _ACTIVITY.get(label, _DEFAULT_ACTIVITY)
            self._hold_until = time.perf_counter() + _MIN_ACTIVITY_HOLD
            self._pending_think = False
        else:  # back to the model — revert to 'thinking' once the current hold elapses
            self._pending_think = True

    def __rich__(self) -> Text:
        now = time.perf_counter()
        emoji, msg = self._activity
        if self._pending_think and now >= self._hold_until:
            emoji, msg = _ACTIVITY[""]
        frame = _BRAILLE[int(now * 12) % len(_BRAILLE)]
        tail = f" · {self._tools} tool{'s' if self._tools != 1 else ''}" if self._tools else ""
        # Bold (no fixed colour) — the main status indicator: uses the terminal's default fg,
        # so it's bright on dark terminals and dark on light ones, always the most prominent
        # line and clearly distinct from the muted-grey tool result lines.
        return Text(f"{frame} {emoji} {msg} · {now - self._start:.0f}s{tail}", style="bold")


def render_banner(console: Console | None = None) -> None:
    """Print the HALIA banner + a one-line hint."""
    con = console or _console
    con.print(Text(HALIA_BANNER, style="bold yellow"))
    con.print(
        "[dim]trust-first agent · Enter to send · Option+Enter for a newline · "
        "/help for commands[/dim]\n"
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
        # If the completion dropdown has a highlighted item, accept it; else send the line.
        buff = event.current_buffer
        state = buff.complete_state
        if state is not None and state.current_completion is not None:
            buff.apply_completion(state.current_completion)
        else:
            buff.validate_and_handle()

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
    return PromptSession(
        key_bindings=build_key_bindings(),
        multiline=True,
        completer=_SlashCompleter(),
        complete_while_typing=True,
        **kwargs,
    )


def run_tui(
    profile: str | None = None,
    allow_commands: bool = False,
    resume: str | None = None,
    max_iters: int = 50,
    allow_local: bool = True,
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
        _chat_cost,
        _chat_export,
        _chat_history,
        _chat_model,
        _chat_procedure,
        _chat_profile,
        _chat_resume,
        _chat_token,
        _chat_undo,
        _make_approver,
        _prepare_context,
        _profile_hint,
        _resumed_age_note,
        _show_step,
        console,
    )
    from halia.cli.slash import human_count
    from halia.config.settings import read_config
    from halia.core.agent import (
        DEFAULT_HISTORY_BUDGET_CHARS,
        SYSTEM_PROMPT,
        RunLimitError,
        converse,
    )
    from halia.core.session import get_session, new_session, save_session
    from halia.permissions.network import allow_local_enabled, set_allow_local
    from halia.providers.base import ProviderError

    set_allow_local(allow_local)

    import os

    from halia.config.settings import ConfigError, is_trusted, load_config, trust_directory

    # Trust boundary first: check if the current directory is trusted.
    cwd = os.getcwd()
    if not is_trusted(cwd):
        from halia.cli.input import pick
        console.print(f"\n[yellow]Working directory:[/yellow] [bold]{cwd}[/bold]")
        options = [
            f"yes — trust {os.path.basename(cwd)}/",
            "no — exit",
        ]
        choice = pick("Trust this directory?", options, default=0)
        if choice.startswith("no"):
            console.print("[dim]exiting.[/dim]")
            return
        trust_directory(cwd)
        console.print(f"[green]✓[/green] trusted [bold]{cwd}[/bold]\n")

    # Config check: on a fresh install (no API key), guide through setup wizard
    # then continue into chat — no dead-end.
    try:
        load_config()
    except ConfigError:
        from halia.cli.input import pick
        console.print(
            "\n[bold yellow]First run detected — no API key configured.[/bold yellow]"
        )
        options = [
            "yes — run halia setup",
            "no — exit",
        ]
        choice = pick("Set up a model provider now?", options, default=0)
        if choice.startswith("no"):
            console.print("[dim]exiting.[/dim]")
            return
        from halia.config.wizard import run_setup
        run_setup(console)
        # Verify setup succeeded before continuing.
        try:
            load_config()
        except ConfigError as exc:
            console.print(f"\n[red]setup incomplete:[/red] {exc}")
            console.print("[dim]run `halia setup` when you're ready.[/dim]")
            return

    render_banner()

    if resume is not None:
        loaded = get_session(resume)
        if loaded is None:
            console.print(f"[yellow]no session '{resume}'[/yellow] — see `halia sessions`.")
            return
        sess = loaded  # narrowed to Session, so /model+/profile can replace() it cleanly
        config, registry, _ = _prepare_context(sess.profile, sess.allow_commands)
        config = replace(config, model=sess.model)
        messages: list[Message] = list(sess.messages)
        archived: list[Message] = list(sess.archived_messages)
        console.print(
            f"[dim]resumed session [bold]{sess.id}[/bold] — {sess.turn_count()} turns, "
            f"last active {_resumed_age_note(sess.updated_at)}[/dim]\n"
        )
    else:
        config, registry, extra_system = _prepare_context(profile, allow_commands)
        messages = [{"role": "system", "content": SYSTEM_PROMPT + extra_system}]
        archived = []
        sess = new_session(config.provider, config.model, profile, allow_commands, messages)
        save_session(sess)

    def persist() -> None:
        save_session(replace(sess, messages=list(messages), archived_messages=list(archived)))

    def farewell() -> None:
        # The resume hint belongs here, on the way OUT — you only resume after closing.
        console.print(
            f"[dim]session {sess.id} — resume with `halia --resume {sess.id}`[/dim]"
        )
        console.print("[dim]bye.[/dim]")

    approve = _make_approver()  # one trust scope for the whole session
    budget = max_iters  # tool-call rounds per turn; raise it live with /iters
    run_profile = sess.profile  # None for the general profile — used to rebuild the registry
    active_profile = run_profile or "general"
    turn_secs = [0.0]  # last turn's wall time (list so the toolbar closure sees updates)
    total_usage = Usage()  # accumulated token usage across the session
    show_tokens = bool(read_config().get("show_tokens", False))  # /token toggles this (persisted)
    if run_profile is None:  # nudge toward a vertical when in the general profile (suppressible)
        _profile_hint()
    footer = _Footer(console)  # live 'working' line during a turn
    streaming = {"on": False}  # is an answer currently streaming to the screen this turn?
    compact_always = {"on": False}  # remembers an "always compact" choice for the session
    pending_image_id: str | None = None  # set by /image, consumed by next user message

    def compact_consent() -> bool:
        # Asked when the window nears full (~85%). yes = once, always = auto for the session,
        # no = skip (fall back to trimming). Mirrors the approval gate's trust model.
        if compact_always["on"]:
            return True
        footer.stop()  # pause the live line for the interactive prompt
        console.print()
        console.print(
            "[bold white on yellow] 🗜️ context nearly full [/bold white on yellow] "
            "compact older turns to keep going?"
        )
        console.print(
            "  [dim]summarises earlier turns; the full transcript is archived and every "
            "tool result stays in the audit trail.[/dim]"
        )
        from halia.cli.input import pick
        options = [
            "yes — compact now",
            "always — auto-compact for this session",
            "no — skip (trim instead)",
        ]
        choice = pick("context nearly full", options, default=0)
        footer.start()
        if choice.startswith("always"):
            compact_always["on"] = True
            return True
        return choice.startswith("yes")

    def on_compact(dropped: list[Message]) -> None:
        archived.extend(dropped)
        persist()
        n = sum(1 for m in dropped if m.get("role") == "user")
        console.print(f"[dim]🗜 compacted {n} earlier turn(s) into a summary.[/dim]")

    def _handle_teach(user_input: str) -> None:
        """Handle /teach <path> [--profile <name>] [--description <text>]."""
        from halia.references import store_reference

        parts = user_input.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            console.print(
                "[yellow]Usage:[/yellow] /teach <path> [--profile qa] [--description \"text\"]\n"
                "  Stores a file as a reference format. The model will follow it.\n"
            )
            return
        # Parse arguments
        args = parts[1].strip()
        path = ""
        profile = ""
        description = ""
        tokens = args.split()
        i = 0
        while i < len(tokens):
            if tokens[i] == "--profile" and i + 1 < len(tokens):
                profile = tokens[i + 1]
                i += 2
            elif tokens[i] == "--description" and i + 1 < len(tokens):
                # Collect everything after --description as the description
                description = " ".join(tokens[i + 1:])
                break
            elif not tokens[i].startswith("--"):
                path = tokens[i]
                i += 1
            else:
                i += 1
        if not path:
            console.print("[yellow]Usage:[/yellow] /teach <path> [--profile qa]\n")
            return
        try:
            ref = store_reference(path, profile=profile, description=description)
            tag = f" → [cyan]{ref.profile}[/cyan]" if ref.profile else ""
            size_kb = f"{ref.size_bytes / 1024:.0f}KB"
            console.print(
                f"[green]✓[/green] Reference stored: [bold]{ref.filename}[/bold]"
                f" ({size_kb}, {ref.file_type}){tag}"
            )
            console.print(
                "  [dim]The model will follow this format when working.[/dim]\n"
            )
        except (FileNotFoundError, ValueError) as exc:
            console.print(f"[red]error:[/red] {exc}\n")

    def _handle_files(user_input: str) -> None:
        """Handle /files [search <query>] to list or search taught files."""
        from halia.references import list_ref_files, search_ref_files

        parts = user_input.split(maxsplit=2)
        if len(parts) >= 2 and parts[1].lower() == "search" and len(parts) >= 3:
            query = parts[2].strip()
            refs = search_ref_files(query)
            if not refs:
                console.print(f"[dim]no files matching '{query}'[/dim]\n")
                return
            console.print(f"[bold]{len(refs)} file(s) matching '{query}'[/bold]")
        else:
            refs = list_ref_files()
            if not refs:
                console.print("[dim]no reference files taught yet. Use /teach to add some.[/dim]\n")
                return
            console.print(f"[bold]{len(refs)} reference file(s)[/bold]")
        for ref in refs:
            tag = f"  [cyan]{ref.profile}[/cyan]" if ref.profile else ""
            size_kb = f"{ref.size_bytes / 1024:.0f}KB"
            desc = f"  [dim]{ref.description}[/dim]" if ref.description else ""
            console.print(
                f"  {ref.filename} [dim]({size_kb}, {ref.file_type})[/dim]{tag}{desc}"
            )
        console.print()

    def ctx_pct() -> int:
        """How full the sent-context window is (a char proxy for tokens)."""
        used = sum(len(str(m.get("content") or "")) for m in messages)
        used += sum(len(str(m.get("tool_calls") or "")) for m in messages)
        return min(100, int(used / DEFAULT_HISTORY_BUDGET_CHARS * 100))

    def status_line() -> str:
        # Printed ABOVE each prompt (persists in the scrollback; never vanishes while the
        # model works). Reverse-video so it reads as a bar that contrasts on BOTH dark and
        # light terminals (the colours swap to whatever the terminal isn't).
        pct = ctx_pct()
        filled = round(pct / 10)
        bar = "▓" * filled + "░" * (10 - filled)
        local = "on" if allow_local_enabled() else "off"
        if not show_tokens:
            tok_seg = "tok --"  # hidden by default (the raw count misleads — see /token, /cost)
        else:
            tok_seg = f"tok {human_count(total_usage.total_tokens)}"
            if total_usage.cached_tokens and total_usage.prompt_tokens:
                cpct = 100 * total_usage.cached_tokens // total_usage.prompt_tokens
                tok_seg += f" · {cpct}% cached"
        return (
            f" {active_profile} · {config.model} · {sess.id[:6]} · "
            f"ctx {bar} {pct}% · budget {budget} · {tok_seg} "
            f"· local {local} · {turn_secs[0]:.1f}s "
        )

    session = build_session()

    while True:
        console.print(status_line(), style="reverse", highlight=False)
        try:
            user_input = session.prompt(PROMPT).strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            farewell()
            break
        if not user_input:
            continue
        if user_input == "/" or user_input.lower() == "/help":
            console.print(_SLASH_HELP)
            continue
        if user_input.lower() in ("/exit", "/quit"):
            farewell()
            break
        if user_input.lower() == "/clear":
            del messages[1:]  # keep the system prompt
            persist()
            console.print("[dim]context cleared.[/dim]\n")
            continue
        if user_input.lower() == "/compact":
            from halia.core.agent import compact_history

            before = ctx_pct()
            console.print("[dim]🗜 compacting…[/dim]")
            dropped = compact_history(messages, config)
            if dropped:
                archived.extend(dropped)
                persist()
                n = sum(1 for m in dropped if m.get("role") == "user")
                console.print(
                    f"[dim]compacted {n} earlier turn(s): ctx {before}% → {ctx_pct()}%.[/dim]\n"
                )
            else:
                console.print("[dim]nothing to compact yet — history is still small.[/dim]\n")
            continue
        if user_input.lower().startswith("/commands"):
            parts = user_input.split()
            if len(parts) >= 2 and parts[1].lower() in ("on", "off"):
                want = parts[1].lower() == "on"
            else:
                want = registry.get("run_command") is None  # bare /commands toggles
            _, registry, _ = _prepare_context(run_profile, want)
            on = registry.get("run_command") is not None
            console.print(
                f"[dim]shell commands {'ON' if on else 'OFF'} — halia "
                f"{'can' if on else 'cannot'} run run_command "
                f"{'(approval-gated)' if on else ''}.[/dim]\n"
            )
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
        if user_input.lower().startswith("/image"):
            parts = user_input.split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                console.print(
                    "[yellow]Usage:[/yellow] /image <path-to-image>\n"
                    "  Stores the image for vision analysis. Supported: PNG, JPG, GIF, WebP.\n"
                )
                continue
            from halia.images import store_image

            img_path = parts[1].strip()
            try:
                img = store_image(img_path)
                w = f"{img.width}×{img.height}" if img.width else "?"
                size_kb = f"{img.size_bytes / 1024:.0f}KB"
                console.print(
                    f"[green]✓[/green] Image stored: {img.id} "
                    f"({w}, {size_kb})\n"
                    f"  [dim]Ask me about this image and I'll analyse it.[/dim]\n"
                )
                # Store the image ID so the next user message can reference it
                pending_image_id = img.id
            except (FileNotFoundError, ValueError) as exc:
                console.print(f"[red]error:[/red] {exc}\n")
            continue
        if user_input.lower().startswith("/teach"):
            _handle_teach(user_input)
            continue
        if user_input.lower().startswith("/files"):
            _handle_files(user_input)
            continue
        if user_input.lower().startswith("/history"):
            _chat_history(user_input, messages)
            continue
        if user_input.lower() == "/cost":
            _chat_cost(total_usage, config.model)
            continue
        if user_input.lower().startswith("/token"):
            show_tokens = _chat_token(user_input, show_tokens)
            continue
        if user_input.lower().startswith("/export"):
            _chat_export(user_input, messages, sess)
            continue
        if user_input.lower().startswith("/model"):
            new_cfg = _chat_model(user_input, config)
            if new_cfg is not None:
                config = new_cfg
                sess = replace(sess, model=config.model)
                persist()
            continue
        if user_input.lower().startswith("/profile"):
            res = _chat_profile(user_input, run_profile, allow_commands, messages)
            if res is not None:
                registry, prof_name = res
                run_profile = active_profile = prof_name
                sess = replace(sess, profile=prof_name)
                persist()
            continue
        if user_input.lower() == "/undo":
            if _chat_undo(messages):
                persist()
            continue
        if user_input.lower().startswith("/resume"):
            _chat_resume(user_input, config, registry)
            continue
        if user_input.lower().startswith("/procedure"):
            to_run = _chat_procedure(user_input)
            if to_run is None:
                continue
            user_input = to_run  # a `/procedure run` — fall through to execute it

        turn_start = len(messages)  # so a failed turn can be rolled back to a valid state
        # If an image was uploaded via /image, attach it to this message.
        if pending_image_id is not None:
            from halia.images import get_image_path

            img_file = get_image_path(pending_image_id)
            if img_file is not None:
                import base64
                import mimetypes

                mime = mimetypes.guess_type(str(img_file))[0] or "image/png"
                b64 = base64.b64encode(img_file.read_bytes()).decode()
                user_content: Any = [
                    {"type": "text", "text": user_input},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ]
            else:
                user_content = user_input
            pending_image_id = None  # consumed
        else:
            user_content = user_input
        messages.append({"role": "user", "content": user_content})
        # The footer shows liveness while halia THINKS (before the first token) and while
        # tools run. The moment the model starts emitting the answer, on_delta stops the
        # footer and STREAMS the tokens live — the text itself becomes the liveness. Tool
        # traces and approval prompts still interrupt cleanly (each closes the stream first).
        streaming["on"] = False  # reset per turn (any prior turn's stream is already closed)

        def close_stream() -> None:
            if streaming["on"]:
                console.print()  # end the streamed line before anything else prints
                streaming["on"] = False

        def approve_and_clear(name: str, arguments: str) -> bool:
            close_stream()
            footer.stop()  # pause the live line for the interactive prompt
            ok = bool(approve(name, arguments))
            footer.start()  # resume (state preserved)
            return ok

        def read_check_and_clear(name: str, arguments: str) -> bool:
            # Read-directory approval (the same gate `halia chat` uses); the live footer
            # must pause for the interactive prompt just like a write approval.
            close_stream()
            footer.stop()
            ok = bool(approve.check_read(name, arguments))
            footer.start()
            return ok

        # `_execute_batch` looks for `approver.check_read`; without this the persona TUI
        # silently skipped the read-directory prompt that `halia chat` enforces.
        approve_and_clear.check_read = read_check_and_clear  # type: ignore[attr-defined]

        def on_activity(label: str) -> None:
            # A new phase (next model call, or a tool) begins — close any streamed answer.
            close_stream()
            # ask_user reads from the terminal — get the footer out of the way so it
            # doesn't animate over the prompt; it resumes on the next activity.
            if label == "ask_user":
                footer.stop()
            else:
                footer.start()  # idempotent; resumes if it was stopped (e.g. after ask_user)
                footer.set_activity(label)

        def on_delta(token: str) -> None:
            if not token:
                return
            if not streaming["on"]:
                footer.stop()  # first token — hand the screen over to the streamed text
                console.print("[bold]halia ›[/bold] ", end="")
                streaming["on"] = True
            console.print(token, end="", markup=False, highlight=False, soft_wrap=True)

        from halia.memory.failures import failures_advisory

        advisory = failures_advisory(user_input)  # Tier 2: warn on similar past failures
        if advisory:
            console.print("  🧠 [dim]recalling a similar past failure[/dim]")
        started = time.perf_counter()
        footer.reset()
        footer.start()
        try:
            # Real on_delta streams the answer token-by-token (and keeps the connection warm —
            # each chunk resets the read timeout, so long generations never time out).
            result = converse(
                messages, config, registry, max_iters=budget,
                observer=_show_step, approver=approve_and_clear,
                on_activity=on_activity, on_delta=on_delta,
                compact_approver=compact_consent, on_compact=on_compact,
                turn_note=advisory,
            )
        except RunLimitError as exc:
            close_stream()
            footer.stop()
            turn_secs[0] = time.perf_counter() - started
            from halia.memory.failures import record_failure

            record_failure(user_input, str(exc), profile or "")
            console.print(f"[red]error:[/red] {exc}")
            console.print(
                f"[dim]raise the budget with /iters {budget * 2} and say 'continue', "
                "or narrow the task.[/dim]\n"
            )
            # The loop stopped between whole batches, so messages are balanced (every
            # assistant tool_calls has its tool responses) — KEEP them and persist, so
            # 'continue' resumes with full context instead of 400-ing on a dangling call.
            persist()
            continue
        except ProviderError as exc:
            close_stream()
            footer.stop()
            turn_secs[0] = time.perf_counter() - started
            from halia.memory.failures import record_failure

            record_failure(user_input, str(exc), profile or "")
            console.print(f"[red]error:[/red] {exc}\n")
            # Roll the whole failed turn back to the last valid state — never leave a
            # half-appended tool exchange (a lone tool_calls with no responses 400s next call).
            del messages[turn_start:]
            continue
        except KeyboardInterrupt:
            # Ctrl-C mid-run: NOT a failure (don't record it). The interrupt can land mid-tool,
            # so roll the whole turn back to a balanced state instead of crashing the TUI.
            close_stream()
            footer.stop()
            turn_secs[0] = time.perf_counter() - started
            console.print("\n[yellow]⏹️ stopped.[/yellow]\n")
            del messages[turn_start:]
            continue
        footer.stop()  # clear the working line before printing the answer
        turn_secs[0] = time.perf_counter() - started
        total_usage = total_usage + result.usage
        messages.append({"role": "assistant", "content": result.answer})
        persist()  # conversation survives a restart from here
        if streaming["on"]:
            console.print()  # newline to close the streamed answer
            streaming["on"] = False
        else:
            # No tokens streamed (e.g. an answer produced without content deltas) — print whole.
            console.print(f"[bold]halia ›[/bold] {escape(result.answer)}")
        if result.unverified:
            figures = ", ".join(result.unverified)
            console.print(f"[yellow]⚠️ unverified figures:[/yellow] {figures}")
        console.print()

        record = new_record(
            config.provider, config.model, user_input, result.answer, result.steps,
            unverified=result.unverified, corrections=result.corrections,
        )
        save_run(record)
