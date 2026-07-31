"""halia CLI entrypoint.

CLI-first (per the requirements): the agent loop lives as a library underneath;
this module just wires the commands. Commands are added as the layers land —
`setup` (wizard), `run`/`chat` (the loop), etc.
"""

from __future__ import annotations

from typing import Annotated

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
        typer.Option("--profile", help="Use a named profile (per-vertical skills/model/prompt)."),
    ] = None,
) -> None:
    """Run halia's agent loop on a task (can use tools)."""
    from dataclasses import replace

    from halia.audit.trace import Step
    from halia.config.settings import ConfigError, load_config
    from halia.core.agent import RunLimitError
    from halia.core.agent import run as run_agent
    from halia.memory.facts import memory_block
    from halia.profiles import get_profile
    from halia.providers.base import ProviderError
    from halia.skills import build_registry, default_registry

    def show(step: Step) -> None:
        console.print(f"[dim]→ {step.tool}({step.arguments})[/dim]")
        console.print(f"[dim]  ↳ {step.preview()}[/dim]")

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
        prof = get_profile(profile)
        if prof is None:
            console.print(f"[red]error:[/red] no profile '{profile}' (see `halia profile list`).")
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
        )
    except (ProviderError, RunLimitError) as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print(result.answer)

    from halia.audit.record import new_record, save_run

    record = new_record(config.provider, config.model, prompt, result.answer, result.steps)
    save_run(record)
    if not quiet:
        console.print(f"[dim](run {record.id} recorded)[/dim]")


@app.command()
def runs(
    limit: Annotated[int, typer.Option(help="How many recent runs to show.")] = 20,
) -> None:
    """List recent runs — the durable audit trail."""
    from halia.audit.record import list_runs

    records = list_runs(limit=limit)
    if not records:
        console.print("[dim]no runs recorded yet.[/dim]")
        return
    for r in records:
        console.print(
            f"[bold]{r.id}[/bold] [dim]{r.started_at}[/dim] "
            f"{r.provider}/{r.model} [dim]({len(r.steps)} steps)[/dim]"
        )
        console.print(f"  [cyan]q[/cyan] {r.prompt[:80]}")
        console.print(f"  [green]a[/green] {r.answer[:80]}")


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
    """List profiles."""
    from halia.profiles import list_profiles

    profiles = list_profiles()
    if not profiles:
        console.print("[dim]no profiles yet — create one with `halia profile create`.[/dim]")
        return
    for prof in profiles:
        console.print(f"[bold]{prof.name}[/bold]  [dim]{prof.model or '(default model)'}[/dim]")
        console.print(f"  skills: {', '.join(prof.skills) or '(calculate only)'}")


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
