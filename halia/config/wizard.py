"""The `halia setup` wizard.

Guided, first-run configuration — trusted directory, provider + model,
then the user pastes their API key. Writes config + stores the key (0600).
No hand-editing of dotfiles.

If an API key already exists for the chosen provider, the wizard offers to
reuse it instead of asking for a new one. Keys are never deleted on switch.
"""

from __future__ import annotations

import os
import threading

from rich.console import Console

from halia.cli.input import ask, pick
from halia.config.settings import (
    CONFIG_FILE,
    PROVIDERS,
    SECRETS_FILE,
    get_trusted_dirs,
    read_config,
    read_secret,
    trust_directory,
    write_config,
    write_secret,
)


def _model_options(models: list[str]) -> list[str]:
    """Model picker options: the curated list plus an always-present
    'Custom model…' choice, so every provider supports any model."""
    options = list(models)
    if "Custom model…" not in options:
        options.append("Custom model…")
    return options


def run_setup(console: Console) -> None:
    """Interactively configure trusted directory, provider, model, and API key."""
    console.print("[bold]halia setup[/bold] — first-time configuration.\n")

    # Step 1: Trusted directory (must be first — controls what halia can access)
    _setup_trust(console)

    # Step 2: Provider + model + API key
    _setup_provider(console)

    # Step 3: Optional system-1 router model (MCP intent routing)
    _setup_router(console)

    # Step 4: MCP servers + secrets (optional — skip to do it later)
    choice = pick(
        "\nConfigure MCP servers now?",
        ["Yes — configure MCP", "Skip — do it later with `halia mcp setup`"],
        default=1,
    )
    if choice.startswith("Yes"):
        from halia.mcp.setup import mcp_setup

        mcp_setup(console)

    # Step 5: CUA — desktop automation
    _setup_cua(console)

    console.print('\nTry it: [bold]halia ask "hello"[/bold]')


def _setup_trust(console: Console) -> None:
    """Configure trusted directories — controls what halia can access."""
    existing = get_trusted_dirs()
    if existing:
        console.print("[dim]Trusted directories:[/dim]")
        for d in existing:
            console.print(f"  [cyan]{d}[/cyan]")
        console.print()
        choice = pick(
            "Add another directory?",
            ["Yes — add a directory", "No — keep current"],
            default=1,
        )
        if choice.startswith("No"):
            return

    # Get the directory to trust
    cwd = os.getcwd()
    console.print(f"\n[dim]Current directory: {cwd}[/dim]")
    path = ask(
        "Directory to trust (path or . for current): ",
        default=".",
    )
    if path == ".":
        path = cwd

    trust_directory(path)
    resolved = os.path.abspath(os.path.expanduser(path))
    console.print(f"[green]✓[/green] trusted [bold]{resolved}[/bold]")


def _setup_provider(console: Console) -> None:
    """Configure provider, model, and API key."""
    console.print("\n[bold]Model provider[/bold]\n")

    names = sorted(PROVIDERS)
    provider = pick("Select provider:", names, default=names.index("deepseek"))
    spec = PROVIDERS[provider]

    # Show the user where to get a key and any provider-specific note.
    if spec.key_url:
        console.print(f"\n[bold]→[/bold] Get your API key at [cyan]{spec.key_url}[/cyan]")
    if spec.note:
        console.print(f"[dim]{spec.note}[/dim]")

    # Model picker — curated list plus an always-present "Custom model…" option,
    # so every provider can select any model (e.g. a newer model before it's curated).
    if spec.models:
        model = pick("\nSelect model:", _model_options(spec.models), default=0)
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

    # Merge into existing config (preserve trusted_dirs, etc.)
    from halia.config.settings import read_config
    existing = read_config()
    existing["provider"] = provider
    existing["model"] = model
    write_config(existing)
    write_secret(provider, api_key)

    console.print(f"\n[green]✓[/green] Settings saved to {CONFIG_FILE}")
    console.print(f"[green]✓[/green] API key saved to {SECRETS_FILE}")
    console.print("[dim]  Locked down so only your user account can read it.[/dim]")


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


def _setup_router(console: Console) -> None:
    """Optionally configure a system-1 router model for MCP intent routing.

    The router reuses the main provider + API key (it's just a different model
    string, e.g. a small/fast one). It's optional: without it, MCP lazy mode
    still works — the agent just calls `mcp_connect` itself instead of being
    preloaded by the router.
    """
    existing = read_config()
    current = existing.get("router_model")
    if current:
        console.print(
            f"\n[dim]Router model (MCP intent routing): [cyan]{current}[/cyan][/dim]"
        )
        choice = pick(
            "Change it?",
            ["No — keep current", "Yes — change", "Remove"],
            default=0,
        )
        if choice.startswith("No"):
            return
        if choice.startswith("Remove"):
            existing.pop("router_model", None)
            write_config(existing)
            console.print("[dim]router model removed.[/dim]")
            return

    choice = pick(
        "\nConfigure a router model (system-1) for MCP intent routing?",
        ["Yes — set a router model", "No — skip (lazy MCP only)"],
        default=1,
    )
    if choice.startswith("No"):
        return
    router = ask(
        "\nRouter model name (system-1 — small/fast; e.g. typesafe/jev-router): "
    ).strip()
    if not router:
        console.print("[dim]skipped — no router model set.[/dim]")
        return
    existing = read_config()
    existing["router_model"] = router
    write_config(existing)
    console.print(f"[green]✓[/green] router model set to [bold]{router}[/bold]")
    console.print("[dim]  Reuses the same provider + API key as the main model.[/dim]")


def _install_python_package(
    console: Console,
    package: str,
    *,
    message: str | None = None,
    timeout: int = 300,
) -> bool:
    """Install a Python package into halia's own running environment.

    Targets the interpreter halia is running under (``sys.executable``), so it
    works whether halia was installed via ``uv tool install``, inside a venv,
    or with plain pip — no active virtual environment is required.
    """
    import shutil
    import subprocess
    import sys

    uv = shutil.which("uv")
    if uv:
        # Explicitly target halia's interpreter; avoids the "No virtual
        # environment found" failure when no venv is active (e.g. uv tools).
        cmd = [uv, "pip", "install", "--python", sys.executable, package]
    else:
        cmd = [sys.executable, "-m", "pip", "install", package]

    with _Spinner(console, message or f"Installing {package}"):
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        console.print(f"[red]  Failed to install {package}: {result.stderr}[/red]")
        return False
    return True


class _Spinner:
    """Minimal spinner context manager — spins while a block of work runs."""

    def __init__(self, console: Console, message: str) -> None:
        self._console = console
        self._message = message
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._done = False

    def __enter__(self) -> _Spinner:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._done = True
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        # The spinner thread writes in place via raw stdout (carriage return, no
        # newline), so clear that line before rich prints the result — otherwise
        # the "✓" lands right after the trailing "..." on the same line.
        import sys

        sys.stdout.write("\r\033[K")
        sys.stdout.flush()
        self._console.print(f"[green]✓[/green] {self._message}")

    def _run(self) -> None:
        import sys
        frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        i = 0
        while not self._stop.is_set():
            frame = frames[i % len(frames)]
            sys.stdout.write(f"\r  \033[36m{frame}\033[0m {self._message}...")
            sys.stdout.flush()
            i += 1
            self._stop.wait(0.08)


def _get_cua_binary_path() -> str:
    """Get the cua-driver binary path, or a fallback message."""
    try:
        from cua_driver import get_binary_path
        return str(get_binary_path())
    except Exception:
        return (
            "(run: uv run python -c "
            "\"from cua_driver import get_binary_path; print(get_binary_path())\")"
        )


def _setup_cua(console: Console) -> None:
    """Install cua-driver and enable CUA backend."""
    console.print(
        "\n[bold]CUA — Computer Use Agent[/bold]\n"
        "\n"
        "CUA driver enables full desktop automation:\n"
        "  • Control any desktop application (not just browser)\n"
        "  • Background operation without stealing focus\n"
        "  • Screenshots, clicks, typing on native apps\n"
        "\n"
        "This installs the cua-driver package (~200MB).\n"
    )

    choice = pick(
        "Install CUA driver?",
        ["Yes — install cua-driver", "No — skip for now"],
        default=0,
    )

    if choice.startswith("No"):
        console.print("[dim]CUA installation skipped.[/dim]")
        return

    _install_cua_driver(console)


def _install_cua_driver(console: Console) -> bool:
    """Install cua-driver into halia's environment and enable blended computer use."""
    # Install cua-driver into halia's own running environment
    if not _install_python_package(
        console, "cua-driver", message="Installing cua-driver", timeout=300
    ):
        console.print("[dim]  You can try later with: halia setup --cua[/dim]")
        return False

    console.print("[green]✓[/green] CUA driver installed and enabled")

    # On headless systems the driver cannot run — warn before the user relies on it.
    from halia.computer.cua_backend import cua_available
    if not cua_available():
        console.print(
            "[yellow]⚠[/yellow] This environment looks headless (no graphical "
            "display) — CUA desktop automation will be unavailable here."
        )

    # On macOS, grant the CUA driver its own TCC permissions (Accessibility +
    # Screen Recording) under the `com.trycua.driver` identity. The embedded host
    # launches the cua-driver binary, so the grants must belong to the BINARY —
    # not to halia's terminal process. Hence `cua-driver permissions grant`
    # (LaunchServices-attributed), not an in-process SDK call. We do NOT start a
    # persistent `serve` daemon: halia spawns the binary itself per session.
    import platform
    if platform.system() == "Darwin":
        console.print(
            "\n[dim]CUA needs Accessibility + Screen Recording permission to click, "
            "type, capture the screen, and show its cursor overlay.[/dim]"
        )
        console.print(
            "[dim]A macOS dialog will appear — approve both requests, then this "
            "continues automatically.[/dim]\n"
        )
        try:
            import subprocess

            from cua_driver import get_binary_path

            with _Spinner(
                console, "Requesting Accessibility + Screen Recording permission"
            ):
                result = subprocess.run(
                    [str(get_binary_path()), "permissions", "grant"],
                    capture_output=True, text=True, timeout=300,
                )
            if result.returncode == 0:
                console.print(
                    "[green]✓[/green] Accessibility + Screen Recording permission granted"
                )
            else:
                detail = (result.stderr or result.stdout or "").strip()
                console.print(
                    f"[yellow]⚠ Permission request returned exit {result.returncode}: "
                    f"{detail}[/yellow]"
                )
        except Exception as exc:
            console.print(f"[yellow]⚠ Could not request permissions: {exc}[/yellow]")
            console.print(
                "[dim]  Run `cua-driver permissions grant` manually, or add cua-driver to:\n"
                "  System Settings → Privacy & Security → Accessibility + Screen Recording\n"
                f"  Binary: {_get_cua_binary_path()}[/dim]"
            )

    console.print(
        "[dim]  Halia uses CUA for all desktop and web tasks — with a visible "
        "agent cursor overlay (your mouse stays free).[/dim]"
    )
    return True
