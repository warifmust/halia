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


def _execute_run(
    prompt: str,
    max_iters: int,
    quiet: bool,
    allow_commands: bool,
    profile: str | None,
    plan: bool = False,
    pause_for_approval: bool = False,
) -> None:
    """Shared body for `run` and the persona-preset commands (`halia finance`, …)."""
    from dataclasses import replace

    from halia.audit.trace import Step
    from halia.config.settings import ConfigError, load_config
    from halia.core.agent import RunLimitError
    from halia.core.agent import run as run_agent
    from halia.memory.facts import memory_block
    from halia.presets import resolve_profile
    from halia.providers.base import ProviderError
    from halia.skills import build_registry, default_registry

    def show(step: Step) -> None:
        console.print(f"[dim]→ {step.tool}({step.arguments})[/dim]")
        console.print(f"[dim]  ↳ {step.preview()}[/dim]")

    def show_plan(text: str) -> None:
        console.print("[cyan]plan[/cyan]")
        console.print(f"[dim]{text}[/dim]\n")

    def approve(name: str, arguments: str) -> bool:
        console.print(f"[yellow]halia wants to run[/yellow] [bold]{name}[/bold]: {arguments}")
        return typer.confirm("Allow?", default=False)

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

    _present_result(config, prompt, result, quiet)


def _present_result(config: Any, prompt: str, result: Any, quiet: bool) -> None:
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
) -> None:
    """Run halia's agent loop on a task (can use tools)."""
    _execute_run(prompt, max_iters, quiet, allow_commands, profile, plan, pause_for_approval)


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

    user = list_profiles()
    user_names = {p.name for p in user}

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


if __name__ == "__main__":
    app()
