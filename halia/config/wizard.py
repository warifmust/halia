"""The `halia setup` wizard.

Guided, first-run configuration — radio-button menus for provider + model,
then the user pastes their API key. Writes config + stores the key (0600).
No hand-editing of dotfiles.

If an API key already exists for the chosen provider, the wizard offers to
reuse it instead of asking for a new one. Keys are never deleted on switch.
"""

from __future__ import annotations

from rich.console import Console

from halia.cli.input import ask, pick
from halia.config.settings import (
    CONFIG_FILE,
    PROVIDERS,
    SECRETS_FILE,
    read_secret,
    write_config,
    write_secret,
)


def run_setup(console: Console) -> None:
    """Interactively configure a provider, model, and API key."""
    console.print("[bold]halia setup[/bold] — configure your model provider.\n")

    names = sorted(PROVIDERS)
    provider = pick("Select provider:", names, default=names.index("deepseek"))
    spec = PROVIDERS[provider]

    # Show the user where to get a key and any provider-specific note.
    if spec.key_url:
        console.print(f"\n[bold]→[/bold] Get your API key at [cyan]{spec.key_url}[/cyan]")
    if spec.note:
        console.print(f"[dim]{spec.note}[/dim]")

    # Model picker — radio buttons if we have a curated list, text input otherwise.
    if spec.models:
        model = pick("\nSelect model:", spec.models, default=0)
        if model == "Custom model…":
            model = ask("\nEnter model name: ")
    else:
        model = (
            ask(f"\nModel ({spec.default_model}): ", default=spec.default_model)
            or spec.default_model
        )

    # API key — reuse an existing stored key if one exists.
    api_key = _resolve_api_key(console, provider)

    if not api_key:
        console.print("[red]No API key entered — aborting.[/red]")
        return

    write_config({"provider": provider, "model": model})
    write_secret(provider, api_key)

    console.print(f"\n[green]✓[/green] Settings saved to {CONFIG_FILE}")
    console.print(f"[green]✓[/green] API key saved to {SECRETS_FILE}")
    console.print("[dim]  Locked down so only your user account can read it.[/dim]")
    console.print('\nTry it: [bold]halia ask "hello"[/bold]')


def _resolve_api_key(console: Console, provider: str) -> str:
    """Get the API key for `provider`, offering to reuse a stored key if one exists."""
    existing = read_secret(provider)
    if existing:
        console.print(
            f"\n[dim]An API key is already stored for {provider}.[/dim]"
        )
        choice = pick(
            "Use this key?",
            ["Yes, reuse the stored key", "No, enter a new key"],
            default=0,
        )
        if choice.startswith("Yes"):
            console.print("[dim]Reusing stored key.[/dim]")
            return existing
    return ask(f"\nAPI key for {provider}: ", is_password=True)
