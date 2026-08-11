"""halia CLI entrypoint.

CLI-first (per the requirements): the agent loop lives as a library underneath;
this module just wires the commands. Commands are added as the layers land —
`setup` (wizard), `run`/`chat` (the loop), etc.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Any

import typer
from rich.console import Console
from rich.highlighter import RegexHighlighter
from rich.theme import Theme

from halia import __version__

if TYPE_CHECKING:  # annotation-only; the runtime imports stay local to keep CLI startup light
    from halia.providers.base import Message


class _ToolHighlighter(RegexHighlighter):
    """Colour a tool call: function=blue, JSON key=yellow, value=green, numbers=green.

    Brackets/commas/braces stay the terminal default (white on dark). Keeps the readable
    multicolour of the default highlighter, minus the red strings that looked like errors.
    """

    base_style = "tool."
    highlights = [
        r"(?P<func>[A-Za-z_]\w*)(?=\()",  # the tool/function name before '('
        r'(?P<key>"[^"]*")(?=\s*:)',  # a JSON key (string right before ':')
        r':\s*(?P<value>"[^"]*")',  # a string value (after ':')
        r"(?P<value>\b(?:true|false|null)\b)",  # literals
        r"(?P<num>-?\d+\.?\d*)",  # numbers
        # HTTP methods, coloured by verb (applied last so they win over the generic value):
        r'(?P<m_get>"GET")',
        r'(?P<m_post>"POST")',
        r'(?P<m_put>"(?:PUT|PATCH)")',
        r'(?P<m_del>"DELETE")',
        r'(?P<m_other>"(?:HEAD|OPTIONS)")',
    ]


_TOOL_THEME = Theme(
    {
        "tool.func": "blue",
        "tool.key": "yellow",
        "tool.value": "green",
        "tool.num": "green",
        "tool.m_get": "bold green",  # safe read
        "tool.m_post": "bold cyan",  # create
        "tool.m_put": "bold yellow",  # update (PUT/PATCH)
        "tool.m_del": "bold red",  # destructive
        "tool.m_other": "bold magenta",
    }
)
_tool_hl = _ToolHighlighter()

app = typer.Typer(
    name="halia",
    help="halia — a trust-first general agent.",
    add_completion=False,
)
console = Console(theme=_TOOL_THEME)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"halia [bold]{__version__}[/bold]")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def _main(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True,
                     help="Show the halia version and exit."),
    ] = False,
    allow_commands: Annotated[
        bool, typer.Option("--allow-commands", help="Enable shell commands (gated by approval).")
    ] = False,
    allow_local: Annotated[
        bool, typer.Option("--allow-local", help="Let http_request reach localhost/LAN.")
    ] = True,
    resume: Annotated[
        str | None, typer.Option("--resume", help="Resume a saved session by id/prefix.")
    ] = None,
    max_iters: Annotated[
        int, typer.Option("--max-iters", help="Tool-call rounds per turn (raise for big tasks).")
    ] = 50,
) -> None:
    """halia — a trust-first general agent. Run with no arguments to open the chat shell."""
    if ctx.invoked_subcommand is None:
        # bare `halia` (and `halia --resume …`, `--allow-local`, …) → open the chat shell.
        from halia.cli.tui import run_tui

        run_tui(
            allow_commands=allow_commands, allow_local=allow_local,
            resume=resume, max_iters=max_iters,
        )


@app.command()
def version() -> None:
    """Show the halia version."""
    console.print(f"halia [bold]{__version__}[/bold]")


@app.command()
def doctor() -> None:
    """Diagnose the local install (config, DB, permission floor, cron, snapshots).

    Read-only: touches no network and creates nothing. Exits non-zero if any check fails.
    """
    from halia.doctor import FAIL, OK, WARN, run_checks

    icon = {OK: "[green]✓[/green]", WARN: "[yellow]![/yellow]", FAIL: "[red]✗[/red]"}
    results = run_checks()
    console.print("[bold]halia doctor[/bold]\n")
    for c in results:
        console.print(f"  {icon.get(c.status, '?')} [bold]{c.name}[/bold] — {c.detail}")

    fails = sum(c.status == FAIL for c in results)
    warns = sum(c.status == WARN for c in results)
    console.print()
    if fails:
        console.print(f"[red]{fails} failed[/red], {warns} warning(s).")
        raise typer.Exit(1)
    if warns:
        console.print(f"[yellow]All checks passed with {warns} warning(s).[/yellow]")
    else:
        console.print("[green]All checks passed.[/green]")


@app.command()
def setup() -> None:
    """Run the first-time setup wizard (provider, model, API key)."""
    from halia.config.wizard import run_setup

    run_setup(console)


@app.command()
def image(
    path: Annotated[str, typer.Argument(help="Path to the image file.")],
) -> None:
    """Store an image for vision analysis."""
    from halia.images import store_image

    try:
        img = store_image(path)
        w = f"{img.width}×{img.height}" if img.width else "?"
        size_kb = f"{img.size_bytes / 1024:.0f}KB"
        console.print(
            f"[green]✓[/green] Image stored: {img.id} ({w}, {size_kb})"
        )
        console.print(f"  [dim]{img.filename}[/dim]")
        console.print(
            "\n[dim]Use /image {img.id} in chat to attach it to a message.[/dim]"
        )
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(1) from exc


@app.command()
def uninstall(
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Skip confirmation prompts."),
    ] = False,
) -> None:
    """Remove halia and all its data (config, secrets, DB, cron jobs).

    Leaves a receipt of what was removed. Use --force to skip confirmations.
    """
    import shutil

    from halia.config.settings import CONFIG_DIR, CONFIG_FILE, SECRETS_FILE
    from halia.schedule import list_jobs, remove_job
    from halia.store.database import DB_PATH

    removed: list[str] = []

    def confirm(msg: str) -> bool:
        if force:
            return True
        from halia.cli.input import ask
        return ask(f"{msg} [y/N] ").strip().lower() in ("y", "yes")

    try:
        # 1. Remove cron entries
        jobs = list_jobs()
        if jobs:
            console.print(f"[cyan]Found {len(jobs)} scheduled job(s):[/cyan]")
            for j in jobs:
                console.print(f"  {j.name} — {j.cron}")
            if confirm("Remove all scheduled jobs?"):
                for j in jobs:
                    remove_job(j.name)
                    removed.append(f"cron: {j.name}")
                console.print(f"  [dim]Removed {len(jobs)} cron job(s).[/dim]")

        # 2. Remove config + secrets
        if CONFIG_FILE.exists() or SECRETS_FILE.exists():
            if confirm("Remove config and API keys (~/.halia/config.json, secrets.json)?"):
                if CONFIG_FILE.exists():
                    CONFIG_FILE.unlink()
                    removed.append("config.json")
                if SECRETS_FILE.exists():
                    SECRETS_FILE.unlink()
                    removed.append("secrets.json")
                console.print("  [dim]Removed config and secrets.[/dim]")

        # 3. Remove database
        if DB_PATH.exists():
            if confirm("Remove database (~/.halia/halia.db)?"):
                DB_PATH.unlink()
                removed.append("halia.db")
                console.print("  [dim]Removed database.[/dim]")

        # 3b. Remove file-write snapshots
        from halia.store.snapshots import SNAPSHOTS_DIR

        if SNAPSHOTS_DIR.exists() and confirm("Remove file-write snapshots (~/.halia/snapshots)?"):
            shutil.rmtree(SNAPSHOTS_DIR, ignore_errors=True)
            removed.append("snapshots/")

        # 4. Remove PERSONA.md
        persona = CONFIG_DIR / "PERSONA.md"
        if persona.exists():
            if confirm("Remove PERSONA.md?"):
                persona.unlink()
                removed.append("PERSONA.md")

        # 5. Remove ~/.halia directory if empty
        try:
            if CONFIG_DIR.exists() and not any(CONFIG_DIR.iterdir()):
                CONFIG_DIR.rmdir()
                removed.append("~/.halia/")
        except OSError:
            pass  # directory not empty or permission denied — leave it

        # 6. Remove the halia binary
        from pathlib import Path as _Path

        which_result = shutil.which("halia")
        bin_path = _Path(which_result) if which_result else _Path("")
        if bin_path.is_file() and confirm(f"Remove halia binary ({bin_path})?"):
            bin_path.unlink()
            removed.append(str(bin_path))
            console.print("  [dim]Removed halia binary.[/dim]")

    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]error during uninstall:[/red] {exc}")
        raise typer.Exit(1) from exc

    # Summary
    if removed:
        console.print(f"\n[green]✓ Removed {len(removed)} item(s):[/green]")
        for item in removed:
            console.print(f"  {item}")
        console.print("\n[dim]halia has been uninstalled.[/dim]")
    else:
        console.print("[dim]Nothing to remove.[/dim]")


@app.command()
def undo(
    path: Annotated[
        str | None,
        typer.Argument(help="File to restore. Omit to undo the most recent write."),
    ] = None,
    show_list: Annotated[
        bool,
        typer.Option("--list", "-l", help="List available snapshots instead of restoring."),
    ] = False,
) -> None:
    """Restore the previous version of a file that write_file overwrote.

    Pop semantics: restoring removes that snapshot, so running undo again peels
    back to the version before it.
    """
    from halia.store.snapshots import list_snapshots, restore_latest

    if show_list:
        rows = list_snapshots(path)
        if not rows:
            console.print("[dim]No snapshots.[/dim]")
            return
        console.print(f"[cyan]Snapshots ({len(rows)}):[/cyan]")
        for orig, created, size in rows:
            console.print(f"  [dim]{created[:19]}[/dim]  {size:>8} B  {orig}")
        return

    restored = restore_latest(path)
    if restored is None:
        where = f" for {path}" if path else ""
        console.print(f"[dim]Nothing to undo{where}.[/dim]")
        raise typer.Exit(1)
    restored_path, nbytes = restored
    console.print(f"[green]✓[/green] Restored {restored_path} ({nbytes} B).")


@app.command()
def use(
    provider: Annotated[
        str, typer.Argument(help="Provider to switch to (openai, deepseek, anthropic, …).")
    ],
    model: Annotated[
        str | None,
        typer.Option("--model", "-m", help="Model to use (defaults to the provider's default)."),
    ] = None,
) -> None:
    """Quickly switch to a different provider without re-entering your API key."""
    from halia.config.settings import (
        PROVIDERS,
        read_config,
        read_secret,
        write_config,
    )

    provider = provider.lower()
    if provider not in PROVIDERS:
        known = ", ".join(sorted(PROVIDERS))
        console.print(f"[red]Unknown provider '{provider}'.[/red] Known: {known}.")
        raise typer.Exit(1)

    spec = PROVIDERS[provider]
    resolved_model = model or spec.default_model

    # Check whether an API key exists for this provider.
    existing_key = read_secret(provider)
    if not existing_key:
        console.print(
            f"[yellow]No API key stored for {provider}.[/yellow] "
            f"Run [bold]halia setup[/bold] to configure it."
        )
        raise typer.Exit(1)

    # Write the new provider + model to config (secrets are untouched).
    data = read_config()
    data["provider"] = provider
    data["model"] = resolved_model
    write_config(data)

    console.print(
        f"[green]✓[/green] Switched to [bold]{provider}[/bold] "
        f"(model: [cyan]{resolved_model}[/cyan]). "
        f"API key was reused."
    )


@app.command()
def ask(prompt: Annotated[str, typer.Argument(help="What to ask halia.")]) -> None:
    """Ask halia a single question (one-shot, no tools)."""
    from halia.config.settings import ConfigError, load_config
    from halia.core.agent import ask as run_ask
    from halia.providers.base import ProviderError

    try:
        config = load_config()
    except ConfigError as exc:
        console.print(f"[red]config error:[/red] {exc}")
        raise typer.Exit(1) from exc

    from halia.core.agent import persona_overlay
    from halia.memory.facts import memory_block
    from halia.memory.failures import failures_advisory, record_failure

    try:
        extra = memory_block(query=prompt) + failures_advisory(prompt) + persona_overlay()
        answer = run_ask(prompt, config, extra_system=extra)
    except ProviderError as exc:
        record_failure(prompt, str(exc))
        console.print(f"[red]provider error:[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print(answer)


def _short_args(arguments: str) -> str:
    """Shorten a tool call's args for display — collapse any big blob to a size.

    Covers strings (content/text), lists (make_excel `sheets`/`rows`, make_chart data),
    and nested dicts — anything whose JSON is long — while keeping short, useful args
    (path, title, name, method, url) visible in full.
    """
    import json

    try:
        obj = json.loads(arguments)
    except (json.JSONDecodeError, TypeError):
        return arguments if len(arguments) <= 300 else arguments[:300] + " …"
    if not isinstance(obj, dict):
        return arguments if len(arguments) <= 300 else arguments[:300] + " …"
    out: dict[str, Any] = {}
    for key, value in obj.items():
        if isinstance(value, str) and len(value) > 80:
            out[key] = f"<{len(value)} chars>"
        elif isinstance(value, list) and len(json.dumps(value)) > 80:
            out[key] = f"<{len(value)} items>"
        elif isinstance(value, dict) and len(json.dumps(value)) > 80:
            out[key] = f"<{len(value)} keys>"
        else:
            out[key] = value
    return json.dumps(out, ensure_ascii=False)


def _show_step(step: Any) -> None:
    # The call line is syntax-highlighted (func=blue, key=yellow, value=green, methods by
    # verb) so it's readable and unmistakably a tool call, not an error. Big content blobs
    # (make_pdf/write_file) are collapsed to a size so the trace stays clean.
    console.print(_tool_hl(f"→ {step.tool}({_short_args(step.arguments)})"))
    # Result line: a muted grey — clearly secondary, and darker than the bold status line.
    console.print(f"  ↳ {step.preview()}", style="grey50", highlight=False, markup=False)


def _write_target_dir(name: str, arguments: str) -> str | None:
    """The absolute directory a file-writing tool targets, or None.

    Covers any tool that writes to a `path` argument (write_file, make_chart, …) — the
    ones consulted here are always dangerous, so read tools with a path never reach this.
    """
    resolved = _write_target_path(arguments)
    import os

    return os.path.dirname(resolved) if resolved is not None else None


def _read_target_dir(name: str, arguments: str) -> str | None:
    """The absolute directory a read tool targets, or None.

    Covers read_file, grep_file, read_csv, read_pdf, read_docx, list_files.
    """
    import json
    import os

    try:
        path = json.loads(arguments).get("path")
    except (json.JSONDecodeError, AttributeError):
        return None
    if isinstance(path, str) and path:
        resolved = os.path.abspath(os.path.expanduser(path))
        return os.path.dirname(resolved)
    return None


def _write_target_path(arguments: str) -> str | None:
    """The absolute file path a file-writing tool targets (~ expanded), or None."""
    import json
    import os

    try:
        path = json.loads(arguments).get("path")
    except (json.JSONDecodeError, AttributeError):
        return None
    if isinstance(path, str) and path:
        return os.path.abspath(os.path.expanduser(path))
    return None


def _generate_diff(name: str, arguments: str) -> str | None:
    """Generate a unified diff for file-writing tools when the file already exists.

    Returns a coloured unified diff string, or None if the diff isn't applicable
    (file doesn't exist, tool isn't write_file, etc.).
    """
    import difflib
    import json
    import os

    if name != "write_file":
        return None

    try:
        obj = json.loads(arguments)
    except (json.JSONDecodeError, TypeError):
        return None

    path = obj.get("path")
    new_content = obj.get("content")
    if not isinstance(path, str) or not isinstance(new_content, str):
        return None

    resolved = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(resolved):
        return None  # new file — no diff to show

    try:
        old_content = open(resolved, encoding="utf-8", errors="replace").read()
    except OSError:
        return None

    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)

    diff = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{os.path.basename(resolved)}",
        tofile=f"b/{os.path.basename(resolved)}",
        lineterm="",
    ))

    if not diff:
        return "[dim]  (no changes)[/dim]"

    # Colour the diff: red for removals, green for additions, blue for headers
    coloured: list[str] = []
    for line in diff:
        if line.startswith("+++") or line.startswith("---"):
            if line.startswith("---"):
                coloured.append(f"[dim red]{line}[/dim red]")
            else:
                coloured.append(f"[dim green]{line}[/dim green]")
        elif line.startswith("@@"):
            coloured.append(f"[bold cyan]{line}[/bold cyan]")
        elif line.startswith("+"):
            coloured.append(f"[green]{line}[/green]")
        elif line.startswith("-"):
            coloured.append(f"[red]{line}[/red]")
        else:
            coloured.append(f"[dim]{line}[/dim]")
    return "\n".join(coloured)


# Natural-language yes: the gate understands "yep / sure / go ahead" as well as "y".
_AFFIRMATIVE = {
    "y", "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "aye", "allow", "allowed",
    "approve", "approved", "proceed", "confirm", "confirmed", "do it", "go ahead", "go",
    "sounds good", "please do", "yes please", "lets do it", "let's do it", "please",
}
# Clear negatives/stop-signals. First decisive word wins, so a leading one of these means no.
_NEGATIVE_CUES = (
    "n, ""no", "nope", "nah", "not", "don't", "dont", "stop", "wait", "hold", "cancel", "never",
)


def _is_affirmative(reply: str) -> bool:
    """First yes/no word wins, so 'yes, no token needed' approves (safe default: False).

    Scanning for the FIRST decisive word — rather than failing on any stray 'no' — means
    'yes, and no bearer token needed' reads as yes, while 'no, wait' and 'not sure' read
    as no. Nothing decisive → False.
    """
    text = reply.strip().lower().rstrip(".!")
    if text in _AFFIRMATIVE:
        return True
    for word in text.split():
        clean = word.strip(",.!?;:")
        if clean in _AFFIRMATIVE:
            return True
        if clean in _NEGATIVE_CUES:
            return False
    return False


def _make_approver() -> Any:
    """A stateful approver: approve once, or trust all writes to a directory this session.

    The prompt is answered in natural language ("yep", "no wait") via `_is_affirmative`;
    anything unclear defaults to No. Trusting a directory only skips the *prompt* — the
    permission floor still applies, so sensitive paths (.ssh, .env, …) are denied
    regardless. Session-scoped; nothing is persisted.
    """
    trusted_dirs: set[str] = set()
    trusted_tools: set[str] = set()
    approved_read_dirs: set[str] = set()  # directories approved for reading

    def approve(name: str, arguments: str) -> bool:
        if name in trusted_tools:
            return True  # already trusted every call to this tool this session
        target_dir = _write_target_dir(name, arguments)
        if target_dir is not None and target_dir in trusted_dirs:
            return True  # already trusted this dir this session — no re-prompt
        console.print()
        console.print(
            f"[bold white on yellow] ⚠ approve [/bold white on yellow] [bold]{name}[/bold]"
        )
        if target_dir is not None:
            # A file write: show the RESOLVED absolute destination and a diff if the file exists.
            console.print(f"  → writes to {_write_target_path(arguments)}", style="white")
            diff = _generate_diff(name, arguments)
            if diff:
                console.print()
                console.print(diff)
                console.print()
        else:
            args_preview = arguments if len(arguments) <= 220 else arguments[:220] + " …"
            console.print(f"  {args_preview}", highlight=False, markup=False, style="white")
        from halia.cli.input import pick
        options = [
            f"yes — allow this {name} call",
            f"always — trust all {name} calls this session",
            "no — stop this call",
        ]
        choice = pick("Select:", options, default=0)
        if choice.startswith("always"):
            if target_dir is not None:
                trusted_dirs.add(target_dir)
            else:
                trusted_tools.add(name)
            console.print(f"  [dim]trusting all {name} calls this session[/dim]\n")
            return True
        if choice.startswith("no") or choice.startswith("stop"):
            console.print()
            return False
        console.print()
        return True

    def check_read(name: str, arguments: str) -> bool:
        """Check if a read tool's target directory is approved. Prompt if not."""
        if name in trusted_tools:
            return True
        import os as _os
        target_dir = _read_target_dir(name, arguments)
        if target_dir is None:
            return True  # no path to check
        if target_dir in approved_read_dirs:
            return True  # already approved this directory
        console.print()
        console.print(f"  🔍 [bold]{name}[/bold] wants to read from [bold]{target_dir}[/bold]")
        from halia.cli.input import pick
        options = [
            f"yes — allow reading from {_os.path.basename(target_dir)}/",
            f"always — trust all reads from {_os.path.basename(target_dir)}/",
            "no — block",
        ]
        choice = pick("Select:", options, default=0)
        if choice.startswith("always"):
            approved_read_dirs.add(target_dir)
            console.print(f"  [dim]trusting reads from {target_dir}[/dim]\n")
            return True
        if choice.startswith("no") or choice.startswith("stop"):
            console.print()
            return False
        console.print()
        return True

    approve.check_read = check_read  # type: ignore[attr-defined]
    return approve


def _prepare_context(
    profile: str | None, allow_commands: bool, query: str | None = None
) -> tuple[Any, Any, str]:
    """Resolve (config, registry, extra_system) from profile/preset + memory + persona.

    Exits with an error message on config/profile problems.
    """
    from dataclasses import replace

    from halia.config.settings import ConfigError, load_config
    from halia.core.agent import persona_overlay
    from halia.memory.facts import memory_block
    from halia.memory.failures import failures_advisory
    from halia.presets import resolve_profile
    from halia.skills import build_registry, default_registry

    try:
        config = load_config()
    except ConfigError as exc:
        console.print(f"[red]config error:[/red] {exc}")
        raise typer.Exit(1) from exc

    advisory = failures_advisory(query) if query else ""
    extra_system = memory_block(query=query) + advisory + persona_overlay()
    if profile is not None:
        prof = resolve_profile(profile)  # user profile wins, else a built-in preset
        if prof is None:
            console.print(
                f"[red]error:[/red] no profile or preset '{profile}' "
                f"(see `halia profile list`)."
            )
            raise typer.Exit(1)
        _mark_profile_used()  # they know profiles exist now — stop nudging in general sessions
        skills = [*prof.skills, "run_command"] if allow_commands else list(prof.skills)
        registry = build_registry(skills)
        if prof.model:
            config = replace(config, model=prof.model)
        if prof.extra_prompt:
            extra_system = f"{extra_system}\n\n{prof.extra_prompt}"
    else:
        registry = default_registry(allow_commands=allow_commands)
    return config, registry, extra_system


def _execute_run(
    prompt: str,
    max_iters: int,
    quiet: bool,
    allow_commands: bool,
    profile: str | None,
    plan: bool = False,
    pause_for_approval: bool = False,
    extra_prompt_block: str = "",
    notify: bool = False,
    unattended: bool = False,
    compact: bool = False,
    budget: int = 0,
    json_output: bool = False,
) -> None:
    """Shared body for `run` and the persona-preset commands (`halia finance`, …).

    `extra_prompt_block` is appended to the system prompt for this run only (used to
    inject a saved test procedure's instructions — see `procedure run`). `notify` pushes
    the result to the configured gateway when the run finishes. `unattended` auto-approves
    gated skills (for scheduled/headless runs) — the permission FLOOR still applies.
    `compact` auto-summarises older turns when the context window fills up (no prompt;
    for long headless/scheduled runs).
    """
    # Honour HALIA_COMPACT_AUTO=true as a default for headless runs without --compact.
    import os

    from halia.core.agent import RunLimitError
    from halia.core.agent import run as run_agent
    from halia.providers.base import ProviderError

    if not compact and os.environ.get("HALIA_COMPACT_AUTO", "").lower() == "true":
        compact = True

    # Honour HALIA_BUDGET_TOKENS env var as default.
    if budget == 0:
        env_budget = os.environ.get("HALIA_BUDGET_TOKENS", "0")
        try:
            budget = int(env_budget)
        except ValueError:
            pass

    # Stdin piping: if input is piped (not a TTY), prepend it to the prompt.
    stdin_data = _read_stdin()
    if stdin_data:
        # Cap at 50k chars to avoid blowing the context window.
        if len(stdin_data) > 50_000:
            stdin_data = stdin_data[:50_000] + "\n… (truncated at 50k chars)"
        prompt = f"Here is the input data:\n\n{stdin_data}\n\n---\n\n{prompt}"

    show = _show_step
    approve_and_read_check = (lambda name, arguments: True) if unattended else _make_approver()
    approve = approve_and_read_check

    def show_plan(text: str) -> None:
        console.print("[cyan]plan[/cyan]")
        console.print(f"[dim]{text}[/dim]\n")

    config, registry, extra_system = _prepare_context(profile, allow_commands, query=prompt)
    if extra_prompt_block:
        extra_system = f"{extra_system}\n\n{extra_prompt_block}".strip()

    try:
        result = run_agent(
            prompt,
            config,
            registry,
            max_iters=max_iters,
            observer=None if quiet else show,
            approver=approve,  # always gate dangerous skills (write_file, run_command)
            extra_system=extra_system,
            plan=plan,
            on_plan=None if quiet else show_plan,
            pause_on_approval=pause_for_approval,
            compact=compact,
            budget_tokens=budget,
        )
    except (ProviderError, RunLimitError) as exc:
        from halia.memory.failures import record_failure

        record_failure(prompt, str(exc), profile or "")  # objective failure → advisory next time
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(1) from exc

    _present_result(config, prompt, result, quiet, notify, json_output=json_output)


def _read_stdin() -> str:
    """Read stdin if it's piped (not a TTY). Returns '' if no piped input."""
    import sys
    if sys.stdin.isatty():
        return ""
    try:
        data = sys.stdin.read()
        return data.strip() if data else ""
    except (EOFError, KeyboardInterrupt):
        return ""


def _present_result(
    config: Any, prompt: str, result: Any, quiet: bool, notify: bool = False,
    json_output: bool = False,
) -> None:
    """Render a finished-or-paused result: paused → checkpoint notice; done → answer + record."""
    if json_output:
        import json as _json

        from halia.audit.record import new_record, save_run

        record = new_record(
            config.provider, config.model, prompt, result.answer, result.steps,
            plan=result.plan, unverified=result.unverified, corrections=result.corrections,
        )
        save_run(record)
        output = {
            "id": record.id,
            "answer": result.answer,
            "steps": [{"tool": s.tool, "arguments": s.arguments} for s in result.steps],
            "unverified": result.unverified,
            "corrections": result.corrections,
            "plan": result.plan,
            "paused": result.paused,
            "usage": {
                "prompt_tokens": result.usage.prompt_tokens,
                "completion_tokens": result.usage.completion_tokens,
                "total_tokens": result.usage.total_tokens,
            },
        }
        console.print(_json.dumps(output, ensure_ascii=False))
        if notify:
            _notify_result(prompt, f"✅ done\n\n{result.answer}")
        return

    if result.paused:
        from halia.core.checkpoint import get_checkpoint

        cp = get_checkpoint(result.checkpoint_id)
        reason = cp.reason if cp else "approval required"
        console.print(f"\n[yellow]⏸ paused[/yellow] — {reason}")
        console.print(f"  checkpoint [bold]{result.checkpoint_id}[/bold]")
        console.print(
            f"  [dim]resume with[/dim] halia resume {result.checkpoint_id} --approve"
            f"  [dim]/[/dim] --deny"
        )
        if notify:
            _notify_result(prompt, f"⏸ paused — {reason} (checkpoint {result.checkpoint_id})")
        return

    console.print(result.answer)

    if result.unverified:
        figures = ", ".join(result.unverified)
        console.print(
            f"[yellow]⚠ Unverified figures[/yellow] (not produced by a tool): {figures}"
        )
    elif result.corrections:
        console.print(
            f"[green]✓ regrounded[/green] [dim](conscience bounced back "
            f"{result.corrections}× to recompute figures via tools)[/dim]"
        )

    from halia.audit.record import new_record, save_run

    record = new_record(
        config.provider,
        config.model,
        prompt,
        result.answer,
        result.steps,
        plan=result.plan,
        unverified=result.unverified,
        corrections=result.corrections,
    )
    save_run(record)
    if not quiet:
        console.print(f"[dim](run {record.id} recorded)[/dim]")

    if notify:
        tail = ""
        if result.unverified:
            tail = f"\n\n⚠ unverified figures: {', '.join(result.unverified)}"
        _notify_result(prompt, f"✅ done\n\n{result.answer}{tail}")


def _notify_result(prompt: str, body: str) -> None:
    """Push a run's outcome to the configured gateway; report but never fail the run."""
    from halia.gateway import notify as gateway_notify

    label = prompt.strip().splitlines()[0][:80] if prompt.strip() else "halia run"
    ok, detail = gateway_notify(f"halia · {label}\n\n{body[:3000]}")
    if ok:
        console.print("[dim](notified via gateway)[/dim]")
    else:
        console.print(f"[yellow]gateway not sent:[/yellow] {detail}")


@app.command()
def run(
    prompt: Annotated[str, typer.Argument(help="The task for halia to work on.")],
    max_iters: Annotated[int, typer.Option(help="Max tool-call iterations.")] = 8,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Hide the tool-call trace.")] = False,
    allow_commands: Annotated[
        bool,
        typer.Option("--allow-commands", help="Enable shell commands (gated by approval)."),
    ] = False,
    profile: Annotated[
        str | None,
        typer.Option("--profile", help="Use a named profile or preset (e.g. finance)."),
    ] = None,
    plan: Annotated[
        bool,
        typer.Option("--plan", help="Draft a short plan before executing (one extra call)."),
    ] = False,
    pause_for_approval: Annotated[
        bool,
        typer.Option(
            "--pause-for-approval",
            help="Unattended mode: freeze into a checkpoint at a dangerous tool instead "
            "of prompting (resume later with `halia resume`).",
        ),
    ] = False,
    notify: Annotated[
        bool, typer.Option("--notify", help="Push the result to the configured gateway.")
    ] = False,
    compact: Annotated[
        bool,
        typer.Option(
            "--compact",
            help="Auto-compact older turns when the context window fills up "
            "(no prompt; for long headless/scheduled runs). Set HALIA_COMPACT_AUTO=true "
            "to enable by default.",
        ),
    ] = False,
    budget: Annotated[
        int,
        typer.Option(
            "--budget",
            help="Max total tokens for the run (0 = unlimited). "
            "Override with HALIA_BUDGET_TOKENS env var.",
        ),
    ] = 0,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output the result as JSON (for scripting/piping)."),
    ] = False,
    allow_local: Annotated[
        bool,
        typer.Option("--allow-local", help="Let http_request reach localhost/LAN (dev testing)."),
    ] = False,
) -> None:
    """Run halia's agent loop on a task (can use tools)."""
    if allow_local:
        from halia.permissions.network import set_allow_local

        set_allow_local(True)
    _execute_run(
        prompt, max_iters, quiet, allow_commands, profile, plan, pause_for_approval,
        notify=notify, compact=compact, budget=budget, json_output=json_output,
    )


def _make_preset_command(preset_name: str) -> Callable[..., None]:
    """Build a command bound to one preset (own scope → no late-binding).

    With a task → one-shot run in that persona. Without a task → open the chat shell in
    that persona (so `halia qa` drops you into the QA TUI, `halia qa "…"` runs it once).
    """

    def _cmd(
        prompt: Annotated[
            str | None,
            typer.Argument(help="Task to run one-shot; omit to open the chat shell."),
        ] = None,
        max_iters: Annotated[int, typer.Option(help="Max tool-call iterations.")] = 8,
        quiet: Annotated[
            bool, typer.Option("--quiet", "-q", help="Hide the tool-call trace.")
        ] = False,
        allow_commands: Annotated[
            bool,
            typer.Option("--allow-commands", help="Enable shell commands (gated by approval)."),
        ] = False,
        allow_local: Annotated[
            bool,
            typer.Option("--allow-local", help="Let http_request reach localhost/LAN."),
        ] = False,
        plan: Annotated[
            bool,
            typer.Option("--plan", help="Draft a short plan before executing (one extra call)."),
        ] = False,
    ) -> None:
        if prompt is None or not prompt.strip():
            from halia.cli.tui import run_tui

            run_tui(profile=preset_name, allow_commands=allow_commands, allow_local=allow_local)
            return
        if allow_local:
            from halia.permissions.network import set_allow_local

            set_allow_local(True)
        _execute_run(prompt, max_iters, quiet, allow_commands, preset_name, plan)

    return _cmd


def _register_preset_commands() -> None:
    """Register one command per built-in preset: `halia qa` (chat) or `halia qa "…"` (one-shot)."""
    from halia.presets import BUILTIN_PRESETS

    for preset_name in BUILTIN_PRESETS:
        app.command(name=preset_name, help=f"Chat in the '{preset_name}' persona (or run a task).")(
            _make_preset_command(preset_name)
        )


_register_preset_commands()


def _chat_footer(result: Any) -> None:
    """Per-turn trust readout in chat (no run record here — the loop records the turn)."""
    if result.unverified:
        figures = ", ".join(result.unverified)
        console.print(f"[yellow]⚠ unverified:[/yellow] {figures}")
    elif result.corrections:
        console.print(f"[dim]✓ regrounded ×{result.corrections}[/dim]")


def _resumed_age_note(updated_at: str) -> str:
    """A human hint of how stale a resumed session is (old context can drift)."""
    from datetime import UTC, datetime

    try:
        then = datetime.fromisoformat(updated_at)
    except ValueError:
        return ""
    delta = datetime.now(UTC) - then
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return "just now"
    if hours < 24:
        return f"{int(hours)}h ago"
    return f"{int(hours // 24)}d ago"


def _ensure_config() -> None:
    """On a fresh install, offer the setup wizard instead of just exiting.

    Returns once a valid config exists, or raises SystemExit if the user declines
    or setup fails.  Trust boundary is NOT checked here — callers handle that first.
    """
    from halia.cli.input import pick
    from halia.config.settings import ConfigError, load_config

    try:
        load_config()
    except ConfigError:
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
            raise typer.Exit(0) from None
        from halia.config.wizard import run_setup

        run_setup(console)
        # Verify setup succeeded before continuing.
        try:
            load_config()
        except ConfigError as exc:
            console.print(f"\n[red]setup incomplete:[/red] {exc}")
            console.print("[dim]run `halia setup` when you're ready.[/dim]")
            raise typer.Exit(1) from exc


def chat(
    profile: Annotated[
        str | None, typer.Option("--profile", help="Use a named profile or preset (e.g. finance).")
    ] = None,
    allow_commands: Annotated[
        bool, typer.Option("--allow-commands", help="Enable shell commands (gated by approval).")
    ] = False,
    resume: Annotated[
        str | None,
        typer.Option("--resume", help="Resume a saved session by id/prefix (`halia sessions`)."),
    ] = None,
) -> None:
    """Talk to halia in a multi-turn conversation (context persists, and survives a restart)."""
    import os
    from dataclasses import replace

    from halia.audit.record import new_record, save_run
    from halia.cli.input import pick
    from halia.config.settings import is_trusted, trust_directory
    from halia.core.agent import SYSTEM_PROMPT, RunLimitError, converse
    from halia.core.checkpoint import list_checkpoints
    from halia.core.session import get_session, new_session, save_session
    from halia.providers.base import ProviderError

    # Trust boundary first: check if the current directory is trusted.
    cwd = os.getcwd()
    if not is_trusted(cwd):
        console.print(f"\n[yellow]Working directory:[/yellow] [bold]{cwd}[/bold]")
        options = [
            f"yes — trust {os.path.basename(cwd)}/",
            "no — exit",
        ]
        choice = pick("Trust this directory?", options, default=0)
        if choice.startswith("no"):
            console.print("[dim]exiting.[/dim]")
            raise typer.Exit(0)
        trust_directory(cwd)
        console.print(f"[green]✓[/green] trusted [bold]{cwd}[/bold]\n")

    # Config check: guide a fresh install through setup, then continue to chat.
    _ensure_config()

    if resume is not None:
        loaded = get_session(resume)
        if loaded is None:
            console.print(
                f"[yellow]no session matching '{resume}'[/yellow] (ambiguous or not found). "
                "See `halia sessions`."
            )
            raise typer.Exit(1)
        session = loaded  # narrowed to Session, so /model+/profile can replace() it cleanly
        # Rebuild the tools from the session's own profile/allow_commands; keep its model.
        config, registry, _ = _prepare_context(session.profile, session.allow_commands)
        config = replace(config, model=session.model)
        messages: list[Message] = list(session.messages)
    else:
        config, registry, extra_system = _prepare_context(profile, allow_commands)
        messages = [{"role": "system", "content": SYSTEM_PROMPT + extra_system}]
        session = new_session(config.provider, config.model, profile, allow_commands, messages)
        save_session(session)  # persist immediately so it shows up in `halia sessions`

    console.print(
        "[bold]halia[/bold] — chat. [dim]/help for all commands · /local toggle local egress · "
        "/image attach a vision image · /resume <id> resume a paused run[/dim]"
    )
    if resume is not None:
        console.print(
            f"[dim]resumed session [bold]{session.id}[/bold] — {session.turn_count()} turns, "
            f"last active {_resumed_age_note(session.updated_at)}[/dim]\n"
        )
    else:
        console.print(f"[dim]session [bold]{session.id}[/bold] — resume later with "
                      f"`halia chat --resume {session.id}`[/dim]\n")

    if profile is None:
        _profile_hint()

    pending = list_checkpoints(limit=3)
    if pending:
        console.print(f"[yellow]⏸ {len(pending)} paused run(s) awaiting a decision:[/yellow]")
        for cp in pending:
            console.print(f"  [bold]{cp.id}[/bold] [dim]{cp.reason}[/dim] — /resume {cp.id}")
        console.print()

    def persist() -> None:
        save_session(replace(session, messages=list(messages)))

    approve = _make_approver()  # one trust scope for the whole chat session
    pending_image_id: str | None = None  # set by /image, consumed by next user message
    from halia.providers.base import Usage

    total_usage = Usage()  # accumulated token usage across the session (for /cost)

    while True:
        try:
            from halia.cli.input import ask
            user_input = ask("you › ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye.[/dim]")
            break
        if not user_input:
            continue
        if user_input.lower() in ("/exit", "/quit"):
            console.print("[dim]bye.[/dim]")
            break
        if user_input == "/" or user_input.lower() == "/help":
            console.print(
                "[bold]slash commands[/bold]\n"
                "  [cyan]/history[/cyan] [n]  show the last n turns (default 10)\n"
                "  [cyan]/cost[/cyan]  session token usage (+ % cached, rough $ estimate)\n"
                "  [cyan]/token[/cyan]  show/hide token usage in the status bar (on/off)\n"
                "  [cyan]/export[/cyan] [path]  save the conversation as markdown\n"
                "  [cyan]/model[/cyan] [name]  show or switch the model\n"
                "  [cyan]/profile[/cyan] [name]  show or switch the profile\n"
                "  [cyan]/undo[/cyan]  drop the last exchange (conversation only)\n"
                "  [cyan]/teach[/cyan]  store a file or URL as a reference (path/URL, --profile)\n"
                "  [cyan]/files[/cyan]  list or search taught reference files\n"
                "  [cyan]/local[/cyan]  toggle local egress (on/off)\n"
                "  [cyan]/commands[/cyan]  toggle shell commands (on/off)\n"
                "  [cyan]/iters[/cyan]  set tool-call budget per turn\n"
                "  [cyan]/compact[/cyan]  summarise older turns\n"
                "  [cyan]/image[/cyan]  attach a vision image\n"
                "  [cyan]/resume[/cyan]  resume a paused run\n"
                "  [cyan]/procedure[/cyan]  manage test procedures\n"
                "  [cyan]/clear[/cyan]  reset conversation\n"
                "  [cyan]/exit[/cyan]  quit"
            )
            continue
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
                set_allow_local(not allow_local_enabled())
            state = "ON" if allow_local_enabled() else "OFF"
            console.print(
                f"[dim]local egress {state} — http_request "
                f"{'can' if allow_local_enabled() else 'cannot'} reach localhost/LAN.[/dim]\n"
            )
            continue
        if user_input.lower().startswith("/commands"):
            parts = user_input.split()
            if len(parts) >= 2 and parts[1].lower() in ("on", "off"):
                want = parts[1].lower() == "on"
            else:
                want = registry.get("run_command") is None
            _, registry, _ = _prepare_context(session.profile, want)
            on = registry.get("run_command") is not None
            console.print(
                f"[dim]shell commands {'ON' if on else 'OFF'} — halia "
                f"{'can' if on else 'cannot'} run run_command "
                f"{'(approval-gated)' if on else ''}.[/dim]\n"
            )
            continue
        if user_input.lower().startswith("/iters"):
            parts = user_input.split()
            if len(parts) >= 2 and parts[1].isdigit() and int(parts[1]) > 0:
                console.print(f"[dim]tool-call budget set to {parts[1]}/turn.[/dim]\n")
            else:
                console.print("[dim]Usage: /iters N[/dim]\n")
            continue
        if user_input.lower() == "/compact":
            from halia.core.agent import compact_history

            console.print("[dim]🗜 compacting…[/dim]")
            dropped = compact_history(messages, config)
            if dropped:
                archived: list[Message] = list(getattr(session, "archived_messages", []))
                archived.extend(dropped)
                persist()
                n = sum(1 for m in dropped if m.get("role") == "user")
                console.print(f"[dim]compacted {n} earlier turn(s).[/dim]\n")
            else:
                console.print("[dim]nothing to compact yet.[/dim]\n")
            continue
        if user_input.lower().startswith("/teach"):
            _handle_teach_chat(user_input)
            continue
        if user_input.lower().startswith("/files"):
            _handle_files_chat(user_input)
            continue
        if user_input.lower().startswith("/image"):
            parts = user_input.split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                console.print(
                    "[yellow]Usage:[/yellow] /image <path-to-image>\n"
                    "  Stores the image for vision analysis. Supported: PNG, JPG, GIF, WebP."
                )
                continue
            from halia.images import store_image

            try:
                img = store_image(parts[1].strip())
                w = f"{img.width}×{img.height}" if img.width else "?"
                size_kb = f"{img.size_bytes / 1024:.0f}KB"
                console.print(
                    f"[green]✓[/green] Image stored: {img.id} ({w}, {size_kb})"
                )
                console.print(
                    "  [dim]Ask me about this image and I'll analyse it.[/dim]"
                )
                pending_image_id = img.id
            except (FileNotFoundError, ValueError) as exc:
                console.print(f"[red]error:[/red] {exc}")
            continue
        if user_input.lower().startswith("/history"):
            _chat_history(user_input, messages)
            continue
        if user_input.lower() == "/cost":
            _chat_cost(total_usage, config.model)
            continue
        if user_input.lower().startswith("/token"):
            from halia.config.settings import read_config as _rc

            _chat_token(user_input, bool(_rc().get("show_tokens", False)))
            continue
        if user_input.lower().startswith("/export"):
            _chat_export(user_input, messages, session)
            continue
        if user_input.lower().startswith("/model"):
            new_cfg = _chat_model(user_input, config)
            if new_cfg is not None:
                config = new_cfg
                session = replace(session, model=config.model)
                persist()
            continue
        if user_input.lower().startswith("/profile"):
            res = _chat_profile(user_input, session.profile, session.allow_commands, messages)
            if res is not None:
                registry, prof_name = res
                session = replace(session, profile=prof_name)
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
        try:
            result = converse(
                messages, config, registry, observer=_show_step, approver=approve
            )
        except (ProviderError, RunLimitError) as exc:
            from halia.memory.failures import record_failure

            record_failure(user_input, str(exc), profile or "")
            console.print(f"[red]error:[/red] {exc}\n")
            messages.pop()  # drop the failed user turn so history stays clean
            continue
        messages.append({"role": "assistant", "content": result.answer})
        total_usage = total_usage + result.usage
        persist()  # conversation survives a restart from here
        console.print(f"[bold]halia ›[/bold] {result.answer}")
        _chat_footer(result)
        console.print()

        record = new_record(
            config.provider, config.model, user_input, result.answer, result.steps,
            unverified=result.unverified, corrections=result.corrections,
        )
        save_run(record)


def _chat_procedure(command: str) -> str | None:
    """Handle `/procedure …` in chat. Returns a prompt to run (for `run`), else None."""
    from halia.procedures import (
        delete_procedure,
        get_procedure,
        list_procedures,
        save_procedure,
    )

    parts = command.split()
    sub = parts[1].lower() if len(parts) > 1 else "list"

    if sub == "list":
        procs = list_procedures()
        if not procs:
            console.print("[dim]no procedures yet — teach one with `/procedure teach`.[/dim]\n")
            return None
        for item in procs:
            status = "[green]ready[/green]" if item.is_runnable() else "[yellow]incomplete[/yellow]"
            console.print(f"  [bold]{item.name}[/bold]  {status}  [dim]{item.target}[/dim]")
        console.print()
        return None

    if sub in ("teach", "add"):
        _teach_procedure(parts[2] if len(parts) > 2 else None)
        console.print()
        return None

    if sub == "show":
        if len(parts) < 3:
            console.print("[dim]usage: /procedure show <name>[/dim]\n")
            return None
        proc = get_procedure(parts[2])
        if proc is None:
            console.print(f"[yellow]no procedure named '{parts[2]}'[/yellow]\n")
            return None
        console.print(proc.to_prompt() + "\n")
        return None

    if sub == "set":
        if len(parts) < 5:
            console.print("[dim]usage: /procedure set <name> <field> <value>[/dim]\n")
            return None
        target_proc = get_procedure(parts[2])
        if target_proc is None:
            console.print(f"[yellow]no procedure named '{parts[2]}'[/yellow]\n")
            return None
        try:
            updated = _apply_field(target_proc, parts[3], " ".join(parts[4:]))
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]\n")
            return None
        save_procedure(updated)
        miss = updated.missing_slots()
        tail = "ready" if not miss else f"still missing {', '.join(miss)}"
        console.print(f"[green]✓[/green] updated {parts[3]} on '{parts[2]}' — {tail}.\n")
        return None

    if sub == "remove":
        if len(parts) < 3:
            console.print("[dim]usage: /procedure remove <name>[/dim]\n")
            return None
        msg = (
            f"[green]✓[/green] deleted '{parts[2]}'"
            if delete_procedure(parts[2])
            else f"[yellow]no procedure named '{parts[2]}'[/yellow]"
        )
        console.print(msg + "\n")
        return None

    if sub == "run":
        if len(parts) < 3:
            console.print("[dim]usage: /procedure run <name> [extra context][/dim]\n")
            return None
        proc = get_procedure(parts[2])
        if proc is None:
            console.print(f"[yellow]no procedure named '{parts[2]}'[/yellow]\n")
            return None
        missing = proc.missing_slots()
        if missing:
            console.print(
                f"[yellow]'{parts[2]}' is incomplete[/yellow] — add {', '.join(missing)} first "
                f"[dim](/procedure teach {parts[2]}).[/dim]\n"
            )
            return None
        task = " ".join(parts[3:])
        run_prompt = f"Run the test procedure '{proc.name}'."
        if task:
            run_prompt = f"{run_prompt} {task}"
        # Inject the procedure's instructions as the turn; the chat loop runs it.
        return f"{proc.to_prompt()}\n\n{run_prompt}"

    console.print(
        "[dim]/procedure list · teach [name] · show <name> · set <name> <field> <value> · "
        "run <name> · remove <name>[/dim]\n"
    )
    return None


def _handle_teach_chat(user_input: str) -> None:
    """Handle /teach inside halia chat."""
    from halia.references import store_reference

    parts = user_input.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        console.print(
            "[yellow]Usage:[/yellow] /teach <path-or-URL> [--profile qa] [--description \"text\"]\n"
            "  Stores a file OR a web page as a reference; the model follows it and cites URLs.\n"
        )
        return
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
            description = " ".join(tokens[i + 1:])
            break
        elif not tokens[i].startswith("--"):
            path = tokens[i]
            i += 1
        else:
            i += 1
    if not path:
        console.print("[yellow]Usage:[/yellow] /teach <path-or-URL> [--profile qa]\n")
        return
    import httpx

    from halia.permissions.network import EgressDenied

    try:
        if path.startswith(("http://", "https://")):
            from halia.references import store_url_reference

            ref = store_url_reference(path, profile=profile, description=description)
            source = f"  [dim]{ref.url}[/dim]"
        else:
            ref = store_reference(path, profile=profile, description=description)
            source = ""
        tag = f" → [cyan]{ref.profile}[/cyan]" if ref.profile else ""
        size_kb = f"{ref.size_bytes / 1024:.0f}KB"
        console.print(
            f"[green]✓[/green] Reference stored: [bold]{ref.filename}[/bold]"
            f" ({size_kb}, {ref.file_type}){tag}{source}"
        )
    except (FileNotFoundError, ValueError, OSError, EgressDenied, httpx.HTTPError) as exc:
        console.print(f"[red]error:[/red] {exc}")


def _handle_files_chat(user_input: str) -> None:
    """Handle /files inside halia chat."""
    from halia.references import list_ref_files, search_ref_files

    parts = user_input.split(maxsplit=2)
    if len(parts) >= 2 and parts[1].lower() == "search" and len(parts) >= 3:
        query = parts[2].strip()
        refs = search_ref_files(query)
        if not refs:
            console.print(f"[dim]no files matching '{query}'[/dim]")
            return
        console.print(f"[bold]{len(refs)} file(s) matching '{query}'[/bold]")
    else:
        refs = list_ref_files()
        if not refs:
            console.print("[dim]no reference files taught yet. Use /teach to add some.[/dim]")
            return
        console.print(f"[bold]{len(refs)} reference file(s)[/bold]")
    for ref in refs:
        tag = f"  [cyan]{ref.profile}[/cyan]" if ref.profile else ""
        size_kb = f"{ref.size_bytes / 1024:.0f}KB"
        desc = f"  [dim]{ref.description}[/dim]" if ref.description else ""
        src = f"  [dim]↗ {ref.url}[/dim]" if ref.url else ""
        console.print(
            f"  {ref.filename} [dim]({size_kb}, {ref.file_type})[/dim]{tag}{src}{desc}"
        )


def _chat_history(command: str, messages: list[Message]) -> None:
    """Handle `/history [n]` — print the last n user/assistant turns (default 10)."""
    from halia.cli.slash import format_history

    parts = command.split()
    n = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() and int(parts[1]) > 0 else 10
    console.print(format_history(messages, n))
    console.print()


def _chat_export(command: str, messages: list[Message], session: Any) -> None:
    """Handle `/export [path]` — write the conversation to a markdown file."""
    from pathlib import Path

    from halia.cli.slash import conversation_markdown

    parts = command.split(maxsplit=1)
    if len(parts) >= 2 and parts[1].strip():
        path = Path(parts[1].strip()).expanduser()
    else:
        path = Path.cwd() / f"halia-{session.id}.md"
    meta = {
        "session": session.id,
        "provider": session.provider,
        "model": session.model,
        "profile": session.profile or "(default)",
        "turns": session.turn_count(),
    }
    md = conversation_markdown(messages, title=f"halia conversation {session.id}", meta=meta)
    try:
        path.write_text(md, encoding="utf-8")
    except OSError as exc:
        console.print(f"[red]error:[/red] could not write {path}: {exc}\n")
        return
    console.print(
        f"[green]✓[/green] exported {session.turn_count()} turn(s) → [bold]{path}[/bold]\n"
    )


def _chat_cost(total_usage: Any, model: str) -> None:
    """Handle `/cost` — token totals (+ % cached) and an optional (rough) dollar estimate."""
    from halia.cli.slash import human_count
    from halia.config.settings import read_config
    from halia.pricing import estimate_cost

    u = total_usage
    console.print(
        f"[bold]tokens this session[/bold]: {human_count(u.total_tokens)} "
        f"[dim]({human_count(u.prompt_tokens)} in + {human_count(u.completion_tokens)} out)[/dim]"
    )
    if u.cached_tokens and u.prompt_tokens:
        pct = 100 * u.cached_tokens // u.prompt_tokens
        console.print(
            f"[dim]{pct}% of input ({human_count(u.cached_tokens)}) served from cache — "
            "billed at the cheaper cache rate.[/dim]"
        )
    est = estimate_cost(
        model, u.prompt_tokens, u.completion_tokens,
        read_config().get("prices"), cached_tokens=u.cached_tokens,
    )
    if est is not None:
        console.print(
            f"[dim]~est ${est:.4f} USD for {model} (cache-adjusted) — rough; "
            "edit prices under 'prices' in ~/.halia/config.json.[/dim]\n"
        )
    else:
        console.print(
            f"[dim]no price on file for '{model}' — tokens only. Add one under 'prices' in "
            "~/.halia/config.json for an estimate.[/dim]\n"
        )


def _profile_hint() -> None:
    """Print the general-profile discoverability hint if not suppressed (bumps the counter).

    Shown only in the general profile; auto-hides once you've used any profile or it's been
    shown a few times; `hints: false` in config turns it off entirely.
    """
    from halia.cli.slash import should_show_profile_hint
    from halia.config.settings import read_config, write_config
    from halia.presets import BUILTIN_PRESETS

    data = read_config()
    if not should_show_profile_hint(data):
        return
    data["general_hint_shows"] = int(data.get("general_hint_shows", 0) or 0) + 1
    write_config(data)
    verticals = " · ".join(sorted(BUILTIN_PRESETS))
    console.print(
        "[dim]You're in the general profile (all tools). For focused work, try a vertical:\n"
        f"  {verticals}\n"
        "  — a tighter, better-selected toolset. Switch anytime with /profile <name>.[/dim]\n"
    )


def _mark_profile_used() -> None:
    """Record that a profile has been activated — suppresses the general-profile hint."""
    from halia.config.settings import read_config, write_config

    data = read_config()
    if not data.get("profile_used"):
        data["profile_used"] = True
        write_config(data)


def _chat_token(command: str, current: bool) -> bool:
    """Handle `/token [on|off]` — toggle the status-bar token display (persisted); returns it."""
    from halia.config.settings import read_config, write_config

    parts = command.split()
    if len(parts) >= 2 and parts[1].lower() in ("on", "true", "off", "false"):
        new = parts[1].lower() in ("on", "true")
    else:
        new = not current
    data = read_config()
    data["show_tokens"] = new
    write_config(data)
    console.print(
        f"[dim]token display {'ON' if new else 'OFF'} — the status bar "
        f"{'shows' if new else 'hides'} token usage.[/dim]\n"
    )
    return new


def _chat_model(command: str, config: Any) -> Any | None:
    """Handle `/model [name]`. Returns a new Config if switched, else None (display/no-op)."""
    from dataclasses import replace

    from halia.cli.slash import available_models

    parts = command.split(maxsplit=1)
    models = available_models(config.provider)
    if len(parts) < 2 or not parts[1].strip():
        listing = ", ".join(models) or "(none listed)"
        console.print(
            f"[bold]model[/bold]: {config.model}  [dim](provider: {config.provider})[/dim]"
        )
        console.print(f"[dim]available: {listing}[/dim]")
        console.print("[dim]switch with: /model <name>[/dim]\n")
        return None
    name = parts[1].strip()
    if name == config.model:
        console.print(f"[dim]already using {name}.[/dim]\n")
        return None
    if models and name not in models:
        console.print(
            f"[yellow]note:[/yellow] '{name}' isn't in {config.provider}'s curated list — "
            "using it anyway."
        )
    console.print(f"[green]✓[/green] model → [bold]{name}[/bold] [dim](from the next turn)[/dim]\n")
    return replace(config, model=name)


def _chat_profile(
    command: str, current_profile: str | None, allow_commands: bool, messages: list[Message]
) -> Any | None:
    """Handle `/profile [name]`. Returns (registry, profile_name) if switched, else None.

    Rebuilds the skill registry and re-personas the live conversation (messages[0]),
    keeping the current provider/model. Validates the name first so an unknown profile
    can't trip `_prepare_context`'s hard exit.
    """
    from halia.core.agent import SYSTEM_PROMPT
    from halia.presets import BUILTIN_PRESETS, resolve_profile
    from halia.profiles import list_profiles

    parts = command.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        builtin = ", ".join(sorted(BUILTIN_PRESETS))
        user = ", ".join(p.name for p in list_profiles())
        console.print(f"[bold]profile[/bold]: {current_profile or '(default/general)'}")
        console.print(f"[dim]built-in: {builtin}[/dim]")
        if user:
            console.print(f"[dim]yours: {user}[/dim]")
        console.print("[dim]switch with: /profile <name>[/dim]\n")
        return None
    name = parts[1].strip()
    if resolve_profile(name) is None:
        console.print(
            f"[red]error:[/red] no profile or preset '{name}' (see `halia profile list`).\n"
        )
        return None
    _, registry, extra_system = _prepare_context(name, allow_commands)
    if messages and messages[0].get("role") == "system":
        messages[0] = {"role": "system", "content": SYSTEM_PROMPT + extra_system}
    console.print(
        f"[green]✓[/green] profile → [bold]{name}[/bold] [dim](skills + persona updated)[/dim]\n"
    )
    return registry, name


def _chat_undo(messages: list[Message]) -> bool:
    """Handle `/undo` — drop the last user+assistant exchange (conversation only)."""
    from halia.cli.slash import drop_last_exchange

    removed = drop_last_exchange(messages)
    if removed == 0:
        console.print("[dim]nothing to undo.[/dim]\n")
        return False
    console.print(
        f"[dim]undid the last exchange ({removed} message(s) dropped). Note: this only edits the "
        "conversation — it does NOT undo file writes or other actions halia already took.[/dim]\n"
    )
    return True


def _chat_resume(command: str, config: Any, registry: Any) -> None:
    """Handle `/resume <id> [approve|deny]` inside chat."""
    from halia.core.agent import resume as resume_run
    from halia.core.checkpoint import delete_checkpoint, get_checkpoint

    parts = command.split()
    if len(parts) < 2:
        console.print("[dim]usage: /resume <checkpoint-id> [approve|deny][/dim]\n")
        return
    cp = get_checkpoint(parts[1])
    if cp is None:
        console.print(f"[yellow]no checkpoint matching '{parts[1]}'[/yellow]\n")
        return
    approve = not (len(parts) >= 3 and parts[2].lower() in ("deny", "no", "n"))
    console.print(f"[dim]{'approved' if approve else 'denied'} — resuming {cp.id}[/dim]")
    result = resume_run(cp, config, approve, observer=_show_step)
    _present_result(config, cp.prompt, result, quiet=False)
    delete_checkpoint(cp.id)
    console.print()


@app.command()
def sessions(
    limit: Annotated[int, typer.Option(help="How many recent sessions to show.")] = 20,
) -> None:
    """List saved chat sessions — resume one with `halia chat --resume <id>`."""
    from halia.core.session import list_sessions

    items = list_sessions(limit=limit)
    if not items:
        console.print("[dim]no saved sessions yet — start one with `halia chat`.[/dim]")
        return
    for s in items:
        persona = f" [dim]({s.profile})[/dim]" if s.profile else ""
        console.print(
            f"[bold]{s.id}[/bold] [dim]{_resumed_age_note(s.updated_at)}[/dim] "
            f"[dim]· {s.turn_count()} turns[/dim]{persona}"
        )
        console.print(f"  [cyan]{s.title or '(empty)'}[/cyan]")
        console.print(f"  [dim]resume:[/dim] halia chat --resume {s.id}")


@app.command()
def runs(
    limit: Annotated[int, typer.Option(help="How many recent runs to show.")] = 20,
    unverified: Annotated[
        bool,
        typer.Option(
            "--unverified", help="Only runs that shipped a figure no tool verified (review set)."
        ),
    ] = False,
) -> None:
    """List recent runs — the durable audit trail."""
    from halia.audit.record import list_runs

    records = list_runs(limit=limit, only_unverified=unverified)
    if not records:
        if unverified:
            console.print("[green]✓ no runs with unverified figures.[/green]")
        else:
            console.print("[dim]no runs recorded yet.[/dim]")
        return
    for r in records:
        tags = []
        if r.plan:
            tags.append("planned")
        if r.corrections:
            tags.append(f"regrounded×{r.corrections}")
        if r.unverified:
            tags.append(f"[yellow]⚠{len(r.unverified)} unverified[/yellow]")
        tag_str = f" [dim]·[/dim] {' '.join(tags)}" if tags else ""
        console.print(
            f"[bold]{r.id}[/bold] [dim]{r.started_at}[/dim] "
            f"{r.provider}/{r.model} [dim]({len(r.steps)} steps)[/dim]{tag_str}"
        )
        console.print(f"  [cyan]q[/cyan] {r.prompt[:80]}")
        console.print(f"  [green]a[/green] {r.answer[:80]}")


@app.command()
def show(
    run_id: Annotated[str, typer.Argument(help="A run id (or unique prefix) from `halia runs`.")],
) -> None:
    """Show one run's full receipts: plan, every step, and the conscience outcome."""
    from halia.audit.record import get_run

    record = get_run(run_id)
    if record is None:
        console.print(f"[yellow]no run matching '{run_id}'[/yellow] (ambiguous or not found).")
        raise typer.Exit(1)

    console.print(f"[bold]{record.id}[/bold] [dim]{record.started_at}[/dim]")
    console.print(f"[dim]{record.provider}/{record.model}[/dim]")
    console.print(f"\n[cyan]prompt[/cyan]\n{record.prompt}")

    if record.plan:
        console.print(f"\n[cyan]plan[/cyan]\n[dim]{record.plan}[/dim]")

    console.print(f"\n[cyan]steps[/cyan] [dim]({len(record.steps)})[/dim]")
    if not record.steps:
        console.print("  [dim](no tool calls)[/dim]")
    for i, step in enumerate(record.steps, 1):
        console.print(f"  [bold]{i}.[/bold] {step.tool}[dim]({step.arguments})[/dim]")
        console.print(f"     [dim]↳ {step.preview(300)}[/dim]")

    console.print(f"\n[green]answer[/green]\n{record.answer}")

    if record.corrections:
        console.print(
            f"\n[green]✓ regrounded[/green] [dim]({record.corrections} corrective "
            f"pass(es) to recompute figures via tools)[/dim]"
        )
    if record.unverified:
        figures = ", ".join(record.unverified)
        console.print(
            f"\n[yellow]⚠ unverified figures[/yellow] (not produced by a tool): {figures}"
        )
    elif not record.corrections:
        console.print("\n[dim]✓ all figures grounded in tool output.[/dim]")


@app.command()
def checkpoints(
    limit: Annotated[int, typer.Option(help="How many recent checkpoints to show.")] = 20,
) -> None:
    """List paused runs awaiting a decision (the HITL approval queue)."""
    from halia.core.checkpoint import list_checkpoints

    cps = list_checkpoints(limit=limit)
    if not cps:
        console.print("[dim]no paused runs.[/dim]")
        return
    for cp in cps:
        console.print(
            f"[bold]{cp.id}[/bold] [dim]{cp.created_at}[/dim] "
            f"[yellow]{cp.reason}[/yellow]"
        )
        console.print(f"  [cyan]q[/cyan] {cp.prompt[:80]}")
        console.print(f"  [dim]resume:[/dim] halia resume {cp.id} --approve  [dim]/[/dim] --deny")


@app.command()
def resume(
    checkpoint_id: Annotated[str, typer.Argument(help="A checkpoint id (or prefix).")],
    approve: Annotated[
        bool,
        typer.Option("--approve/--deny", help="Approve or deny the pending action."),
    ] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Hide the tool-call trace.")] = False,
) -> None:
    """Resume a paused run, applying your approve/deny decision to the pending action."""
    from dataclasses import replace

    from halia.audit.trace import Step
    from halia.config.settings import ConfigError, load_config
    from halia.core.agent import RunLimitError
    from halia.core.agent import resume as resume_run
    from halia.core.checkpoint import delete_checkpoint, get_checkpoint
    from halia.providers.base import ProviderError

    cp = get_checkpoint(checkpoint_id)
    if cp is None:
        console.print(
            f"[yellow]no checkpoint matching '{checkpoint_id}'[/yellow] (ambiguous or not found)."
        )
        raise typer.Exit(1)

    try:
        config = load_config()
    except ConfigError as exc:
        console.print(f"[red]config error:[/red] {exc}")
        raise typer.Exit(1) from exc
    config = replace(config, model=cp.model)  # honor the model the run started with

    def show(step: Step) -> None:
        console.print(f"[dim]→ {step.tool}({step.arguments})[/dim]")
        console.print(f"[dim]  ↳ {step.preview()}[/dim]")

    decision = "approved" if approve else "denied"
    console.print(f"[dim]{decision} — resuming {cp.id}[/dim]\n")
    try:
        result = resume_run(
            cp, config, approve, observer=None if quiet else show
        )
    except (ProviderError, RunLimitError) as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(1) from exc

    _present_result(config, cp.prompt, result, quiet)
    # This checkpoint is spent — the run either finished or minted a new checkpoint.
    delete_checkpoint(cp.id)


@app.command()
def remember(
    fact: Annotated[str, typer.Argument(help="A fact for halia to remember across runs.")],
) -> None:
    """Save a fact halia will recall in future runs."""
    from halia.memory.facts import remember as save_fact

    saved = save_fact(fact)
    console.print(f"[green]✓[/green] remembered ([bold]{saved.id}[/bold]): {saved.content}")


@app.command()
def memory() -> None:
    """List what halia remembers."""
    from halia.memory.facts import list_facts

    facts = list_facts()
    if not facts:
        console.print("[dim]nothing remembered yet — add with `halia remember \"…\"`.[/dim]")
        return
    for fact in facts:
        console.print(f"[bold]{fact.id}[/bold] [dim]{fact.created_at}[/dim]  {fact.content}")


@app.command()
def forget(
    fact_id: Annotated[str, typer.Argument(help="The id (or prefix) of the fact to forget.")],
) -> None:
    """Remove a remembered fact."""
    from halia.memory.facts import forget as forget_fact

    if forget_fact(fact_id):
        console.print(f"[green]✓[/green] forgot {fact_id}")
    else:
        console.print(f"[yellow]no fact matching '{fact_id}'[/yellow]")


@app.command()
def failures(
    forget: Annotated[
        str | None, typer.Option("--forget", help="Forget one failure by id (or prefix).")
    ] = None,
    clear: Annotated[bool, typer.Option("--clear", help="Forget ALL recorded failures.")] = False,
) -> None:
    """Show (or prune) the objective run failures halia recalls to avoid repeating.

    Recorded automatically on a hard failure (iteration cap / provider error) and surfaced as an
    advisory on similar future tasks. Inspectable and forgettable — prune a stale lesson here.
    """
    from halia.memory.failures import forget_failure, list_failures

    if forget:
        msg = f"[green]✓[/green] forgot {forget}" if forget_failure(forget) else (
            f"[yellow]no failure matching '{forget}'[/yellow]"
        )
        console.print(msg)
        return
    items = list_failures()
    if clear:
        for f in items:
            forget_failure(f.id)
        console.print(f"[green]✓[/green] cleared {len(items)} failure(s).")
        return
    if not items:
        console.print("[dim]no failures recorded.[/dim]")
        return
    for f in items:
        prof = f" [cyan]{f.profile}[/cyan]" if f.profile else ""
        console.print(f"[bold]{f.id}[/bold] [dim]{f.created_at[:19]}[/dim]{prof}")
        console.print(f"  task: {f.prompt[:100]}")
        console.print(f"  [red]cause:[/red] {f.cause}")


profile_app = typer.Typer(help="Manage profiles (per-vertical skill/model/prompt sets).")
app.add_typer(profile_app, name="profile")


@profile_app.command("create")
def profile_create(
    name: Annotated[str, typer.Argument(help="Profile name.")],
    skill: Annotated[
        list[str] | None, typer.Option("--skill", help="A skill to enable (repeatable).")
    ] = None,
    model: Annotated[str | None, typer.Option("--model", help="Model override.")] = None,
    prompt: Annotated[str, typer.Option("--prompt", help="Extra system prompt / persona.")] = "",
) -> None:
    """Create (or replace) a profile."""
    from halia.profiles import Profile, save_profile
    from halia.skills import available_skills

    skills = skill or []
    unknown = [s for s in skills if s not in available_skills()]
    if unknown:
        console.print(f"[red]unknown skills:[/red] {', '.join(unknown)}")
        console.print(f"[dim]available: {', '.join(available_skills())}[/dim]")
        raise typer.Exit(1)
    save_profile(Profile(name=name, skills=skills, model=model, extra_prompt=prompt))
    console.print(f"[green]✓[/green] saved profile '[bold]{name}[/bold]' ({len(skills)} skills).")


@profile_app.command("list")
def profile_list() -> None:
    """List profiles (yours + built-in persona presets)."""
    from halia.presets import BUILTIN_PRESETS
    from halia.profiles import list_profiles
    from halia.skills import DEFAULT_SKILLS

    user = list_profiles()
    user_names = {p.name for p in user}

    console.print("[bold]default[/bold] [dim](no profile)[/dim]")
    console.print(
        f"  [bold]general assistant[/bold]  [dim]all {len(DEFAULT_SKILLS)} tools — "
        f"just `halia run \"…\"` or `halia chat`[/dim]"
    )
    console.print("[bold]built-in presets[/bold] [dim](run as `halia <name> \"…\"`)[/dim]")
    for name, prof in sorted(BUILTIN_PRESETS.items()):
        tag = " [dim](overridden by your profile)[/dim]" if name in user_names else ""
        console.print(f"  [bold]{name}[/bold]{tag}  [dim]{len(prof.skills)} skills[/dim]")

    console.print("[bold]your profiles[/bold]")
    if not user:
        console.print("  [dim]none yet — create one with `halia profile create`.[/dim]")
        return
    for prof in user:
        console.print(f"  [bold]{prof.name}[/bold]  [dim]{prof.model or '(default model)'}[/dim]")
        console.print(f"    skills: {', '.join(prof.skills) or '(calculate only)'}")


@profile_app.command("delete")
def profile_delete(name: Annotated[str, typer.Argument(help="Profile name.")]) -> None:
    """Delete a profile."""
    from halia.profiles import delete_profile

    if delete_profile(name):
        console.print(f"[green]✓[/green] deleted profile '{name}'")
    else:
        console.print(f"[yellow]no profile named '{name}'[/yellow]")


schedule_app = typer.Typer(help="Schedule procedures via the OS crontab (no daemon).")
app.add_typer(schedule_app, name="schedule")


@schedule_app.command("add")
def schedule_add(
    name: Annotated[str, typer.Argument(help="A name for this schedule.")],
    procedure: Annotated[str, typer.Option("--procedure", help="Procedure to run.")],
    cron: Annotated[
        str, typer.Option("--cron", help="Cron spec, e.g. '0 9 * * *' or '@daily'.")
    ],
    notify: Annotated[
        bool, typer.Option("--notify", help="Push the result to the gateway when it runs.")
    ] = False,
) -> None:
    """Schedule a procedure to run on a cron. Writes an OS crontab entry (the OS times it)."""
    from halia.procedures import get_procedure
    from halia.schedule import ScheduleError, add_job, build_procedure_command, validate_cron

    proc = get_procedure(procedure)
    if proc is None:
        console.print(f"[red]no procedure named '{procedure}'[/red] (see `halia procedure list`).")
        raise typer.Exit(1)
    if not proc.is_runnable():
        console.print(
            f"[red]procedure '{procedure}' is incomplete[/red] — "
            f"missing {', '.join(proc.missing_slots())}. Fill it before scheduling."
        )
        raise typer.Exit(1)
    if notify:
        from halia.gateway import get_gateway

        if get_gateway() is None:
            console.print(
                "[yellow]note:[/yellow] --notify set but no gateway configured — "
                "run `halia gateway setup` or it won't send."
            )
    try:
        validate_cron(cron)
        job = add_job(name, cron, build_procedure_command(procedure, notify))
    except ScheduleError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(
        f"[green]✓[/green] scheduled '[bold]{job.name}[/bold]' — {job.cron} → {procedure}"
    )
    console.print(f"  [dim]{job.command}[/dim]")


@schedule_app.command("list")
def schedule_list() -> None:
    """List halia-managed cron jobs."""
    from halia.schedule import ScheduleError, list_jobs

    try:
        jobs = list_jobs()
    except ScheduleError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(1) from exc
    if not jobs:
        console.print("[dim]no scheduled jobs — add one with `halia schedule add`.[/dim]")
        return
    for job in jobs:
        console.print(
            f"  [bold]{job.name}[/bold]  [cyan]{job.cron}[/cyan]  [dim]{job.command}[/dim]"
        )


@schedule_app.command("remove")
def schedule_remove(name: Annotated[str, typer.Argument(help="Schedule name.")]) -> None:
    """Remove a scheduled job."""
    from halia.schedule import ScheduleError, remove_job

    try:
        removed = remove_job(name)
    except ScheduleError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(1) from exc
    if removed:
        console.print(f"[green]✓[/green] removed schedule '{name}'")
    else:
        console.print(f"[yellow]no schedule named '{name}'[/yellow]")


gateway_app = typer.Typer(help="Configure outbound notifications (e.g. Telegram).")
app.add_typer(gateway_app, name="gateway")


@gateway_app.command("setup")
def gateway_setup() -> None:
    """Configure a notify channel (Telegram): bot token + chat id, stored locally."""
    from halia.gateway import save_gateway

    console.print("[bold]Gateway setup[/bold] [dim](send-only notifications)[/dim]")
    console.print("Channel: [bold]telegram[/bold] (the only channel for now).")
    console.print(
        "[dim]Create a bot with @BotFather to get a token; message it, then find your "
        "chat id via @userinfobot.[/dim]"
    )
    token = typer.prompt("Telegram bot token", hide_input=True).strip()
    chat_id = typer.prompt("Chat id (where to send)").strip()
    if not token or not chat_id:
        console.print("[yellow]both a token and a chat id are required — nothing saved.[/yellow]")
        raise typer.Exit(1)
    save_gateway("telegram", chat_id, token)
    console.print("[green]✓[/green] gateway saved. Test it with `halia gateway test`.")


@gateway_app.command("status")
def gateway_status() -> None:
    """Show whether a gateway is configured (token is never printed)."""
    from halia.gateway import get_gateway

    gw = get_gateway()
    if gw is None:
        console.print("[yellow]no gateway configured[/yellow] — run `halia gateway setup`.")
        return
    console.print(f"[green]configured[/green] — channel [bold]{gw.channel}[/bold], "
                  f"chat id {gw.chat_id} [dim](token stored, hidden)[/dim]")


@gateway_app.command("test")
def gateway_test() -> None:
    """Send a test message through the configured gateway."""
    from halia.gateway import notify

    ok, detail = notify("👋 halia gateway test — you're all set.")
    if ok:
        console.print("[green]✓[/green] sent — check your channel.")
    else:
        console.print(f"[red]failed:[/red] {detail}")
        raise typer.Exit(1)


procedure_app = typer.Typer(help="Teach, list, and run reusable test procedures.")
app.add_typer(procedure_app, name="procedure")


@procedure_app.command("add")
def procedure_add(
    name: Annotated[str, typer.Argument(help="Procedure name (how you'll run it).")],
    target: Annotated[str, typer.Option("--target", help="What is under test.")] = "",
    data_spec: Annotated[
        str, typer.Option("--data", help="Test-data spec (synthesize …, or 'user provides').")
    ] = "",
    method: Annotated[str, typer.Option("--method", help="HTTP method.")] = "GET",
    url: Annotated[str, typer.Option("--url", help="Endpoint URL.")] = "",
    pass_rule: Annotated[
        str, typer.Option("--pass-rule", help="Deterministic pass/fail rule.")
    ] = "",
    column: Annotated[
        list[str] | None,
        typer.Option("--column", help="An output CSV column (repeatable)."),
    ] = None,
    step: Annotated[
        list[str] | None,
        typer.Option("--step", help="An ordered step for a multi-step procedure (repeatable)."),
    ] = None,
    header: Annotated[
        list[str] | None,
        typer.Option("--header", help="A default request header 'Name: value' (repeatable)."),
    ] = None,
    provided: Annotated[
        bool,
        typer.Option("--provided", help="Test data is provided by you (gated/real), not made up."),
    ] = False,
    description: Annotated[str, typer.Option("--description", help="Short description.")] = "",
) -> None:
    """Create (or replace) a test procedure. Missing required slots are reported, not fatal."""
    from halia.procedures import Procedure, save_procedure

    headers: dict[str, str] = {}
    for item in header or []:
        if ":" not in item:
            console.print(f"[red]bad --header (want 'Name: value'):[/red] {item}")
            raise typer.Exit(1)
        key, value = item.split(":", 1)
        headers[key.strip()] = value.strip()

    proc = Procedure(
        name=name,
        description=description,
        target=target,
        data_spec=data_spec,
        data_source="provided" if provided else "synthesize",
        steps=step or [],
        method=method.upper(),
        url=url,
        headers=headers,
        result_columns=column or [],
        pass_rule=pass_rule,
    )
    save_procedure(proc)
    console.print(f"[green]✓[/green] saved procedure '[bold]{name}[/bold]'.")
    missing = proc.missing_slots()
    if missing:
        console.print(
            f"[yellow]incomplete[/yellow] — fill before running: {', '.join(missing)} "
            f"[dim](edit with `halia procedure add {name} …`)[/dim]"
        )


@procedure_app.command("list")
def procedure_list() -> None:
    """List saved test procedures."""
    from halia.procedures import list_procedures

    procs = list_procedures()
    if not procs:
        console.print("[dim]no procedures yet — teach one with `halia procedure add`.[/dim]")
        return
    for proc in procs:
        status = "[green]ready[/green]" if proc.is_runnable() else "[yellow]incomplete[/yellow]"
        target = proc.target or "[dim](no target)[/dim]"
        console.print(f"  [bold]{proc.name}[/bold]  {status}  [dim]{target}[/dim]")


@procedure_app.command("show")
def procedure_show(name: Annotated[str, typer.Argument(help="Procedure name.")]) -> None:
    """Show a procedure's full rendered instructions."""
    from halia.procedures import get_procedure

    proc = get_procedure(name)
    if proc is None:
        console.print(f"[yellow]no procedure named '{name}'[/yellow]")
        raise typer.Exit(1)
    console.print(proc.to_prompt())
    missing = proc.missing_slots()
    if missing:
        console.print(f"\n[yellow]missing required slots:[/yellow] {', '.join(missing)}")


@procedure_app.command("remove")
def procedure_remove(name: Annotated[str, typer.Argument(help="Procedure name.")]) -> None:
    """Delete a procedure."""
    from halia.procedures import delete_procedure

    if delete_procedure(name):
        console.print(f"[green]✓[/green] deleted procedure '{name}'")
    else:
        console.print(f"[yellow]no procedure named '{name}'[/yellow]")


def _collect_provided_data(data_file: str | None) -> str:
    """For a 'provided'-data procedure: turn a file path or pasted rows into a prompt block."""
    import os

    if data_file:
        return (
            "The user has provided the test data in a file. Read it with read_csv and use "
            f"those rows EXACTLY — do NOT synthesize: {data_file}"
        )
    console.print(
        "[cyan]This procedure uses data you provide.[/cyan] Enter a file path, "
        "or paste rows and end with an empty line:"
    )
    from halia.cli.input import ask
    first = ask("› ").strip()
    if not first:
        return ""  # nothing supplied — to_prompt still tells the model to ask
    if os.path.isfile(os.path.expanduser(first)):
        return (
            "The user has provided the test data in a file. Read it with read_csv and use "
            f"those rows EXACTLY — do NOT synthesize: {first}"
        )
    lines = [first]
    while True:
        more = ask("› ")
        if not more.strip():
            break
        lines.append(more)
    return (
        "The user provided this test data; use it EXACTLY and do NOT invent rows:\n"
        + "\n".join(lines)
    )


@procedure_app.command("run")
def procedure_run(
    name: Annotated[str, typer.Argument(help="Procedure name to run.")],
    task: Annotated[
        str, typer.Argument(help="Optional extra context for this run (e.g. data to use).")
    ] = "",
    data_file: Annotated[
        str | None,
        typer.Option("--data-file", help="Path to your test data (for 'provided'-data procs)."),
    ] = None,
    max_iters: Annotated[int, typer.Option(help="Max tool-call iterations.")] = 12,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Hide the tool-call trace.")] = False,
    profile: Annotated[
        str | None,
        typer.Option("--profile", help="Run under a profile/preset (default: general)."),
    ] = None,
    plan: Annotated[bool, typer.Option("--plan", help="Draft a short plan first.")] = False,
    pause_for_approval: Annotated[
        bool,
        typer.Option("--pause-for-approval", help="Unattended: checkpoint at a dangerous tool."),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Auto-approve gated tools (scheduled runs; floor applies)."),
    ] = False,
    notify: Annotated[
        bool, typer.Option("--notify", help="Push the result to the configured gateway.")
    ] = False,
    allow_local: Annotated[
        bool,
        typer.Option("--allow-local", help="Let http_request reach localhost/LAN (dev testing)."),
    ] = False,
) -> None:
    """Execute a saved test procedure (injects its instructions into the run)."""
    if allow_local:
        from halia.permissions.network import set_allow_local

        set_allow_local(True)
    from halia.procedures import get_procedure

    proc = get_procedure(name)
    if proc is None:
        console.print(f"[yellow]no procedure named '{name}'[/yellow]")
        raise typer.Exit(1)
    missing = proc.missing_slots()
    if missing:
        console.print(
            f"[red]procedure '{name}' is incomplete[/red] — missing {', '.join(missing)}.\n"
            f"[dim]fill it with `halia procedure add {name} …`, then run.[/dim]"
        )
        raise typer.Exit(1)

    extra_block = proc.to_prompt()
    if proc.provides_own_data():
        data_block = _collect_provided_data(data_file)
        if data_block:
            extra_block = f"{extra_block}\n\n{data_block}"

    run_prompt = f"Run the test procedure '{name}'."
    if task:
        run_prompt = f"{run_prompt} {task}"
    _execute_run(
        run_prompt,
        max_iters,
        quiet,
        allow_commands=False,
        profile=profile,
        plan=plan,
        pause_for_approval=pause_for_approval,
        extra_prompt_block=extra_block,
        notify=notify,
        unattended=yes,
    )


# --- The friendly teach flow (shared by `procedure teach` and chat `/procedure teach`) ---

_METHODS_SET = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}


def _ask_slot(label: str, current: str = "") -> str:
    """Ask one slot warmly; enter keeps the current value."""
    from halia.cli.input import ask

    hint = f" [dim](enter to keep: {current})[/dim]" if current else ""
    console.print(f"[cyan]{label}[/cyan]{hint}")
    answer = ask("› ").strip()
    return answer or current


def _parse_endpoint(answer: str, cur_method: str, cur_url: str) -> tuple[str, str]:
    """Parse a 'METHOD https://…' answer into (method, url); tolerant of just a URL."""
    parts = answer.split()
    if not parts:
        return cur_method, cur_url
    if parts[0].upper() in _METHODS_SET:
        method = parts[0].upper()
        url = parts[1] if len(parts) > 1 else cur_url
        return method, url
    return cur_method or "GET", parts[0]


def _parse_headers(answer: str, current: dict[str, str]) -> dict[str, str]:
    """Parse ';'-separated 'Name: value' pairs; 'none' clears; blank keeps current."""
    if not answer:
        return current
    if answer.lower() in ("none", "-", "skip"):
        return {}
    headers: dict[str, str] = {}
    for item in answer.split(";"):
        item = item.strip()
        if ":" in item:
            key, value = item.split(":", 1)
            headers[key.strip()] = value.strip()
    return headers or current


# field name (+ aliases) → canonical slot, for editing one slot at a time.
_FIELD_ALIASES = {
    "target": "target",
    "data": "data_spec", "data_spec": "data_spec", "data-spec": "data_spec",
    "source": "data_source", "data_source": "data_source", "data-source": "data_source",
    "steps": "steps", "step": "steps",
    "method": "method",
    "url": "url",
    "endpoint": "endpoint",  # "METHOD URL" → sets both
    "pass": "pass_rule", "pass_rule": "pass_rule", "pass-rule": "pass_rule",
    "passrule": "pass_rule", "rule": "pass_rule",
    "description": "description", "desc": "description",
    "columns": "result_columns", "column": "result_columns", "result_columns": "result_columns",
    "header": "header", "headers": "header",
}
_SETTABLE = (
    "target, data, source, steps, method, url, endpoint, pass-rule, columns, header, description"
)


def _apply_field(proc: Any, field: str, value: str) -> Any:
    """Return a copy of `proc` with one field changed. Raises ValueError on bad input."""
    from dataclasses import replace

    key = _FIELD_ALIASES.get(field.lower().strip())
    if key is None:
        raise ValueError(f"unknown field '{field}'. Settable: {_SETTABLE}")
    if key == "endpoint":
        method, url = _parse_endpoint(value, proc.method, proc.url)
        return replace(proc, method=method, url=url)
    if key == "method":
        method = value.strip().upper()
        if method not in _METHODS_SET:
            raise ValueError(f"method must be one of {', '.join(sorted(_METHODS_SET))}")
        return replace(proc, method=method)
    if key == "data_source":
        source = value.strip().lower()
        if source not in ("synthesize", "provided"):
            raise ValueError("data-source must be 'synthesize' or 'provided'")
        return replace(proc, data_source=source)
    if key == "result_columns":
        cols = [c.strip() for c in value.split(",") if c.strip()]
        return replace(proc, result_columns=cols)
    if key == "steps":
        steps = [s.strip() for s in value.split(";") if s.strip()]
        return replace(proc, steps=steps)
    if key == "header":
        if ":" not in value:
            raise ValueError("header must be 'Name: value'")
        hkey, hval = value.split(":", 1)
        headers = dict(proc.headers)
        headers[hkey.strip()] = hval.strip()
        return replace(proc, headers=headers)
    return replace(proc, **{key: value.strip()})


def _teach_procedure(name_arg: str | None) -> None:
    """Interactively teach (or edit) a test procedure — asks warmly, then saves."""
    from halia.cli.input import ask
    from halia.procedures import Procedure, get_procedure, save_procedure

    name = name_arg or ask("Name this test procedure › ").strip()
    if not name:
        console.print("[yellow]I'll need a name to save it — nothing stored.[/yellow]")
        return

    cur = get_procedure(name)
    if cur is not None:
        console.print(f"[dim]Editing '{name}'. Press enter to keep each value as-is.[/dim]\n")
    else:
        cur = Procedure(name=name)
        console.print(
            f"[dim]Let's set up '{name}'. Answer what you can — enter skips a field.[/dim]\n"
        )

    target = _ask_slot("What are we testing? (e.g. POST /auth/login)", cur.target)
    data_spec = _ask_slot(
        "What test data does it need? Describe the rows (their shape/columns).",
        cur.data_spec,
    )
    source_answer = _ask_slot(
        "Should I SYNTHESIZE that data, or will you PROVIDE it yourself "
        "(e.g. real/gated accounts)? [synthesize/provided]",
        cur.data_source,
    )
    data_source = "provided" if source_answer.strip().lower().startswith("prov") else "synthesize"
    steps_answer = _ask_slot(
        "Any ordered steps? (first do X; then Y; …) — ';'-separate, or blank for a single "
        "endpoint call.",
        "; ".join(cur.steps),
    )
    steps = [s.strip() for s in steps_answer.split(";") if s.strip()]
    method, url = _parse_endpoint(
        _ask_slot(
            "Which endpoint should I call? Method and URL (e.g. POST https://api…/login)",
            f"{cur.method} {cur.url}".strip(),
        ),
        cur.method,
        cur.url,
    )
    headers = _parse_headers(
        _ask_slot(
            "Any default headers, like an auth token? (e.g. Authorization: Bearer {token}; "
            "';'-separate, 'none' to clear)",
            "; ".join(f"{k}: {v}" for k, v in cur.headers.items()),
        ),
        cur.headers,
    )
    cols_answer = _ask_slot(
        "What columns should the results CSV have? "
        "(comma-separated, e.g. test_id, email, actual_status, verdict)",
        ", ".join(cur.result_columns),
    )
    result_columns = [c.strip() for c in cols_answer.split(",") if c.strip()]
    pass_rule = _ask_slot(
        "How do we know a case passed? A clear, checkable rule "
        "(e.g. actual_status == expect_status)",
        cur.pass_rule,
    )
    description = _ask_slot("A one-line description? (optional)", cur.description)

    proc = Procedure(
        name=name,
        description=description,
        target=target,
        data_spec=data_spec,
        data_source=data_source,
        steps=steps,
        method=method,
        url=url,
        headers=headers,
        result_columns=result_columns,
        pass_rule=pass_rule,
    )
    save_procedure(proc)
    console.print(f"\n[green]✓ saved[/green] '[bold]{name}[/bold]'.")
    missing = proc.missing_slots()
    if missing:
        console.print(
            f"[yellow]Still incomplete[/yellow] — it won't run until you add: "
            f"{', '.join(missing)}. [dim](just `teach {name}` again to fill them in.)[/dim]"
        )
    else:
        console.print(
            f"[green]Ready to run[/green] — `halia procedure run {name}` "
            f"[dim](or `/procedure run {name}` in chat).[/dim]"
        )


@procedure_app.command("teach")
def procedure_teach(
    name: Annotated[
        str | None, typer.Argument(help="Procedure name (prompted if omitted).")
    ] = None,
) -> None:
    """Teach a test procedure conversationally (asks only what it needs, then saves)."""
    _teach_procedure(name)


@procedure_app.command("set")
def procedure_set(
    name: Annotated[str, typer.Argument(help="Procedure name.")],
    field: Annotated[str, typer.Argument(help=f"Field to change: {_SETTABLE}.")],
    value: Annotated[str, typer.Argument(help="New value.")],
) -> None:
    """Change a single field of a saved procedure (no need to re-teach the rest)."""
    from halia.procedures import get_procedure, save_procedure

    proc = get_procedure(name)
    if proc is None:
        console.print(f"[yellow]no procedure named '{name}'[/yellow]")
        raise typer.Exit(1)
    try:
        updated = _apply_field(proc, field, value)
    except ValueError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(1) from exc
    save_procedure(updated)
    console.print(f"[green]✓[/green] updated [bold]{field}[/bold] on '{name}'.")
    missing = updated.missing_slots()
    status = (
        "[green]ready to run[/green]"
        if not missing
        else f"[yellow]still incomplete[/yellow] — missing {', '.join(missing)}"
    )
    console.print(f"  {status}")


if __name__ == "__main__":
    app()
