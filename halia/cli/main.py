"""halia CLI entrypoint.

CLI-first (per the requirements): the agent loop lives as a library underneath;
this module just wires the commands. Commands are added as the layers land —
`setup` (wizard), `run`/`chat` (the loop), etc.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

import typer
from rich.console import Console

from halia import __version__

app = typer.Typer(
    name="halia",
    help="halia — a trust-first general agent.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"halia [bold]{__version__}[/bold]")
        raise typer.Exit()


@app.callback()
def _main(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True,
                     help="Show the halia version and exit."),
    ] = False,
) -> None:
    """halia — a trust-first general agent."""


@app.command()
def version() -> None:
    """Show the halia version."""
    console.print(f"halia [bold]{__version__}[/bold]")


@app.command()
def setup() -> None:
    """Run the first-time setup wizard (provider, model, API key)."""
    from halia.config.wizard import run_setup

    run_setup(console)


@app.command()
def tui(
    vertical: Annotated[
        str | None,
        typer.Argument(help="Optional vertical/profile to run in (e.g. qa, finance)."),
    ] = None,
    profile: Annotated[
        str | None, typer.Option("--profile", help="Vertical/profile (same as the argument).")
    ] = None,
    allow_commands: Annotated[
        bool, typer.Option("--allow-commands", help="Enable shell commands (gated by approval).")
    ] = False,
    resume: Annotated[
        str | None, typer.Option("--resume", help="Resume a saved session by id/prefix.")
    ] = None,
) -> None:
    """REPL-style chat shell (banner, rich input, streaming, sessions). `halia tui qa` for QA."""
    from halia.cli.tui import run_tui

    run_tui(profile=vertical or profile, allow_commands=allow_commands, resume=resume)


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

    from halia.memory.facts import memory_block

    try:
        answer = run_ask(prompt, config, extra_system=memory_block())
    except ProviderError as exc:
        console.print(f"[red]provider error:[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print(answer)


def _show_step(step: Any) -> None:
    console.print(f"[dim]→ {step.tool}({step.arguments})[/dim]")
    console.print(f"[dim]  ↳ {step.preview()}[/dim]")


def _write_target_dir(name: str, arguments: str) -> str | None:
    """The absolute directory a file-writing tool targets, or None.

    Covers any tool that writes to a `path` argument (write_file, make_chart, …) — the
    ones consulted here are always dangerous, so read tools with a path never reach this.
    """
    import json
    import os

    try:
        path = json.loads(arguments).get("path")
    except (json.JSONDecodeError, AttributeError):
        return None
    return os.path.dirname(os.path.abspath(path)) if isinstance(path, str) and path else None


# Natural-language yes: the gate understands "yep / sure / go ahead" as well as "y".
_AFFIRMATIVE = {
    "y", "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "aye", "allow", "allowed",
    "approve", "approved", "proceed", "confirm", "confirmed", "do it", "go ahead", "go",
    "sounds good", "please do", "yes please", "lets do it", "let's do it", "please",
}
# Cues that mean "not a clean yes" — negation OR a correction ("yes, but change the url").
_NEGATIVE_CUES = (
    "no", "nope", "nah", "not", "don't", "dont", "stop", "wait", "hold", "cancel",
    "but", "change", "instead", "except", "actually", "hmm",
)


def _is_affirmative(reply: str) -> bool:
    """True only for a clear yes with no negation/correction cue (safe default: False)."""
    text = reply.strip().lower().rstrip(".!")
    if not text:
        return False
    words = set(text.split())
    if any(cue in words for cue in _NEGATIVE_CUES):
        return False
    return text in _AFFIRMATIVE or bool(words & _AFFIRMATIVE)


def _make_approver() -> Any:
    """A stateful approver: approve once, or trust all writes to a directory this session.

    The prompt is answered in natural language ("yep", "no wait") via `_is_affirmative`;
    anything unclear defaults to No. Trusting a directory only skips the *prompt* — the
    permission floor still applies, so sensitive paths (.ssh, .env, …) are denied
    regardless. Session-scoped; nothing is persisted.
    """
    trusted_dirs: set[str] = set()

    def approve(name: str, arguments: str) -> bool:
        target_dir = _write_target_dir(name, arguments)
        if target_dir is not None and target_dir in trusted_dirs:
            return True  # already trusted this dir this session — no re-prompt
        console.print(f"[yellow]halia wants to run[/yellow] [bold]{name}[/bold]: {arguments}")
        if target_dir is not None:
            choice = console.input(
                "Allow? [bold]yes[/bold] / [bold]a[/bold]ll writes to this folder / "
                "[bold]no[/bold]: "
            ).strip().lower()
            if choice in ("a", "all"):
                trusted_dirs.add(target_dir)
                console.print(f"[dim]trusting writes to {target_dir} for this session[/dim]")
                return True
            return _is_affirmative(choice)
        return _is_affirmative(console.input("Allow? "))

    return approve


def _prepare_context(profile: str | None, allow_commands: bool) -> tuple[Any, Any, str]:
    """Resolve (config, registry, extra_system) from a profile/preset + memory. Exits on error."""
    from dataclasses import replace

    from halia.config.settings import ConfigError, load_config
    from halia.memory.facts import memory_block
    from halia.presets import resolve_profile
    from halia.skills import build_registry, default_registry

    try:
        config = load_config()
    except ConfigError as exc:
        console.print(f"[red]config error:[/red] {exc}")
        raise typer.Exit(1) from exc

    extra_system = memory_block()
    if profile is not None:
        prof = resolve_profile(profile)  # user profile wins, else a built-in preset
        if prof is None:
            console.print(
                f"[red]error:[/red] no profile or preset '{profile}' "
                f"(see `halia profile list`)."
            )
            raise typer.Exit(1)
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
) -> None:
    """Shared body for `run` and the persona-preset commands (`halia finance`, …).

    `extra_prompt_block` is appended to the system prompt for this run only (used to
    inject a saved test procedure's instructions — see `procedure run`). `notify` pushes
    the result to the configured gateway when the run finishes. `unattended` auto-approves
    gated skills (for scheduled/headless runs) — the permission FLOOR still applies.
    """
    from halia.core.agent import RunLimitError
    from halia.core.agent import run as run_agent
    from halia.providers.base import ProviderError

    show = _show_step
    approve = (lambda name, arguments: True) if unattended else _make_approver()

    def show_plan(text: str) -> None:
        console.print("[cyan]plan[/cyan]")
        console.print(f"[dim]{text}[/dim]\n")

    config, registry, extra_system = _prepare_context(profile, allow_commands)
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
        )
    except (ProviderError, RunLimitError) as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(1) from exc

    _present_result(config, prompt, result, quiet, notify)


def _present_result(
    config: Any, prompt: str, result: Any, quiet: bool, notify: bool = False
) -> None:
    """Render a finished-or-paused result: paused → checkpoint notice; done → answer + record."""
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
) -> None:
    """Run halia's agent loop on a task (can use tools)."""
    _execute_run(
        prompt, max_iters, quiet, allow_commands, profile, plan, pause_for_approval, notify=notify
    )


def _make_preset_command(preset_name: str) -> Callable[..., None]:
    """Build a `run`-style command bound to one preset (own scope → no late-binding)."""

    def _cmd(
        prompt: Annotated[str, typer.Argument(help="The task for halia to work on.")],
        max_iters: Annotated[int, typer.Option(help="Max tool-call iterations.")] = 8,
        quiet: Annotated[
            bool, typer.Option("--quiet", "-q", help="Hide the tool-call trace.")
        ] = False,
        allow_commands: Annotated[
            bool,
            typer.Option("--allow-commands", help="Enable shell commands (gated by approval)."),
        ] = False,
        plan: Annotated[
            bool,
            typer.Option("--plan", help="Draft a short plan before executing (one extra call)."),
        ] = False,
    ) -> None:
        _execute_run(prompt, max_iters, quiet, allow_commands, preset_name, plan)

    return _cmd


def _register_preset_commands() -> None:
    """Register one command per built-in preset, so `halia finance "…"` works directly."""
    from halia.presets import BUILTIN_PRESETS

    for preset_name in BUILTIN_PRESETS:
        app.command(name=preset_name, help=f"Run halia in the '{preset_name}' persona.")(
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


@app.command()
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
    from dataclasses import replace

    from halia.audit.record import new_record, save_run
    from halia.core.agent import SYSTEM_PROMPT, RunLimitError, converse
    from halia.core.checkpoint import list_checkpoints
    from halia.core.session import get_session, new_session, save_session
    from halia.providers.base import Message, ProviderError

    if resume is not None:
        session = get_session(resume)
        if session is None:
            console.print(
                f"[yellow]no session matching '{resume}'[/yellow] (ambiguous or not found). "
                "See `halia sessions`."
            )
            raise typer.Exit(1)
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
        "[bold]halia[/bold] — chat. [dim]/exit quit · /clear reset · /resume <id> paused run · "
        "/procedure teach|list|run a test procedure[/dim]"
    )
    if resume is not None:
        console.print(
            f"[dim]resumed session [bold]{session.id}[/bold] — {session.turn_count()} turns, "
            f"last active {_resumed_age_note(session.updated_at)}[/dim]\n"
        )
    else:
        console.print(f"[dim]session [bold]{session.id}[/bold] — resume later with "
                      f"`halia chat --resume {session.id}`[/dim]\n")

    pending = list_checkpoints(limit=3)
    if pending:
        console.print(f"[yellow]⏸ {len(pending)} paused run(s) awaiting a decision:[/yellow]")
        for cp in pending:
            console.print(f"  [bold]{cp.id}[/bold] [dim]{cp.reason}[/dim] — /resume {cp.id}")
        console.print()

    def persist() -> None:
        save_session(replace(session, messages=list(messages)))

    approve = _make_approver()  # one trust scope for the whole chat session

    while True:
        try:
            user_input = console.input("[cyan]you ›[/cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye.[/dim]")
            break
        if not user_input:
            continue
        if user_input.lower() in ("/exit", "/quit"):
            console.print("[dim]bye.[/dim]")
            break
        if user_input.lower() == "/clear":
            del messages[1:]  # keep the system prompt
            persist()
            console.print("[dim]context cleared.[/dim]\n")
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
        try:
            result = converse(
                messages, config, registry, observer=_show_step, approver=approve
            )
        except (ProviderError, RunLimitError) as exc:
            console.print(f"[red]error:[/red] {exc}\n")
            messages.pop()  # drop the failed user turn so history stays clean
            continue
        messages.append({"role": "assistant", "content": result.answer})
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
    first = console.input("  › ").strip()
    if not first:
        return ""  # nothing supplied — to_prompt still tells the model to ask
    if os.path.isfile(os.path.expanduser(first)):
        return (
            "The user has provided the test data in a file. Read it with read_csv and use "
            f"those rows EXACTLY — do NOT synthesize: {first}"
        )
    lines = [first]
    while True:
        more = console.input("  › ")
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
) -> None:
    """Execute a saved test procedure (injects its instructions into the run)."""
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
    hint = f" [dim](enter to keep: {current})[/dim]" if current else ""
    console.print(f"[cyan]{label}[/cyan]{hint}")
    answer = console.input("  › ").strip()
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
    from halia.procedures import Procedure, get_procedure, save_procedure

    name = name_arg or console.input("[cyan]Name this test procedure ›[/cyan] ").strip()
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
