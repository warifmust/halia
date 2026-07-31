"""The `halia setup` wizard.

Guided, first-run configuration — the user answers a few prompts and the wizard
writes the config + stores the API key (0600). No hand-editing of dotfiles.
"""

from __future__ import annotations

from rich.console import Console
from rich.prompt import Prompt

from halia.config.settings import (
    CONFIG_FILE,
    PROVIDERS,
    SECRETS_FILE,
    write_config,
    write_secret,
)


def run_setup(console: Console) -> None:
    """Interactively configure a provider, model, and API key."""
    console.print("[bold]halia setup[/bold] — configure your model provider.\n")

    provider = Prompt.ask("Provider", choices=sorted(PROVIDERS), default="deepseek")
    spec = PROVIDERS[provider]
    model = Prompt.ask("Model", default=spec.default_model)
    api_key = Prompt.ask(f"API key for {provider}", password=True)

    if not api_key.strip():
        console.print("[red]No API key entered — aborting.[/red]")
        return

    write_config({"provider": provider, "model": model})
    write_secret(provider, api_key.strip())

    console.print(f"\n[green]✓[/green] Settings saved to {CONFIG_FILE}")
    console.print(f"[green]✓[/green] API key saved to {SECRETS_FILE}")
    console.print("[dim]  Locked down so only your user account can read it.[/dim]")
    console.print('\nTry it: [bold]halia ask "hello"[/bold]')
