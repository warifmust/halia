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

    try:
        answer = run_ask(prompt, config)
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
) -> None:
    """Run halia's agent loop on a task (can use tools)."""
    from halia.audit.trace import Step
    from halia.config.settings import ConfigError, load_config
    from halia.core.agent import RunLimitError
    from halia.core.agent import run as run_agent
    from halia.providers.base import ProviderError
    from halia.skills import default_registry

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

    try:
        result = run_agent(
            prompt,
            config,
            default_registry(allow_commands=allow_commands),
            max_iters=max_iters,
            observer=None if quiet else show,
            approver=approve if allow_commands else None,
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


if __name__ == "__main__":
    app()
