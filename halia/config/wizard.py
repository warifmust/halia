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
    read_secret,
    trust_directory,
    write_config,
    write_secret,
)


def run_setup(console: Console) -> None:
    """Interactively configure trusted directory, provider, model, and API key."""
    console.print("[bold]halia setup[/bold] — first-time configuration.\n")

    # Step 1: Trusted directory (must be first — controls what halia can access)
    _setup_trust(console)

    # Step 2: Provider + model + API key
    _setup_provider(console)

    # Step 3: Halia computer — browser automation
    _setup_computer(console)

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


def _setup_computer(console: Console) -> None:
    """Offer to enable halia computer (browser automation via Playwright)."""
    from halia.config.settings import read_config

    config = read_config()
    if config.get("computer_enabled"):
        console.print("\n[dim]halia computer is already enabled.[/dim]")
        return

    console.print(
        "\n[bold]halia computer[/bold] — browser automation\n"
        "\n"
        "halia can control a browser for automation tasks:\n"
        "  • Fill forms, click buttons, navigate websites\n"
        "  • Take screenshots for visual verification\n"
        "  • Run automated tests on web applications\n"
        "\n"
        "This requires Playwright (browser engine).\n"
    )

    choice = pick(
        "Enable halia computer?",
        ["Yes — install Playwright (~200MB)", "No — skip for now (can add later)"],
        default=0,
    )

    if choice.startswith("Yes"):
        console.print("\n[dim]Installing Playwright...[/dim]")
        success = _install_playwright(console)
        if success:
            config["computer_enabled"] = True
            _pick_computer_backend(console, config)
        else:
            console.print("[yellow]⚠️[/yellow] Playwright installation failed")
            console.print("[dim]  You can try later with: halia setup --computer[/dim]")
    else:
        config["computer_enabled"] = False
        write_config(config)
        console.print(
            "[dim]halia computer skipped. "
            "Enable later with: halia setup --computer[/dim]"
        )


def _pick_computer_backend(console: Console, config: dict) -> None:
    """Ask user to choose between halia computer and CUA."""
    console.print(
        "\n[bold]Which computer use do you prefer?[/bold]\n"
    )

    choice = pick(
        "",
        [
            "Halia computer — lightweight, guarded by halia's trust layer",
            "CUA — full computer-use agent from cua.ai, more powerful",
        ],
        default=0,
    )

    if choice.startswith("CUA"):
        config["computer_backend"] = "cua"
        write_config(config)
        console.print("[green]✓[/green] CUA selected")
        console.print("[dim]  Run `halia setup --cua` to install the CUA agent.[/dim]")
    else:
        config["computer_backend"] = "halia"
        write_config(config)
        console.print("[green]✓[/green] halia computer enabled")
        console.print("[dim]  Use browser_open, browser_click, etc. to automate.[/dim]")


def _install_playwright(console: Console) -> bool:
    """Install Playwright via the project's browser extra, then install Chromium."""
    import shutil
    import subprocess

    uv = shutil.which("uv")
    pip_cmd = [uv, "pip", "install", "-e", ".[browser]"] if uv else ["pip", "install", "playwright"]

    # ── Step 1: install Python package with spinner ──
    with _Spinner(console, "Installing playwright via project dependencies"):
        result = subprocess.run(pip_cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        console.print(f"[red]  Failed to install playwright: {result.stderr}[/red]")
        return False

    # ── Step 2: install Chromium with progress ──
    _run_with_chromium_progress(console)
    return True


def _run_with_chromium_progress(console: Console) -> None:
    """Run `playwright install chromium` with a live progress line."""
    import re
    import subprocess
    import sys

    proc = subprocess.Popen(
        ["playwright", "install", "chromium"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    percent_re = re.compile(r"(\d{1,3})%")
    last_pct = -1
    line = ""

    while True:
        ch = proc.stdout.read(1)  # type: ignore[union-attr]
        if ch == "" and proc.poll() is not None:
            break
        if ch in ("\r", "\n"):
            stripped = line.strip()
            if not stripped:
                line = ""
                continue
            # extract percentage if present
            m = percent_re.search(stripped)
            if m:
                pct = int(m.group(1))
                if pct != last_pct:
                    last_pct = pct
                    # build a compact progress line
                    filled = pct // 5  # 20 chars total
                    bar = "█" * filled + "░" * (20 - filled)
                    sys.stdout.write(f"\r  \033[36m⬇\033[0m [{bar}] {pct:3d}%")
                    sys.stdout.flush()
            else:
                # non-progress line — just show it
                sys.stdout.write(f"\r  \033[36m⬇\033[0m {stripped[:60]}\n")
                sys.stdout.flush()
            line = ""
        else:
            line += ch

    proc.wait()

    if last_pct >= 0:
        # clear the progress line and show done
        sys.stdout.write("\r" + " " * 50 + "\r")
        sys.stdout.flush()
    if proc.returncode != 0:
        console.print("[yellow]  ⚠ Chromium install returned non-zero exit code[/yellow]")
    else:
        console.print("[green]✓[/green] Chromium browser installed")


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
        # clear the spinner line
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
    import shutil
    import subprocess

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

    # Install cua-driver
    uv = shutil.which("uv")
    pip_cmd = [uv, "pip", "install", "-e", ".[cua]"] if uv else ["pip", "install", "cua-driver"]

    with _Spinner(console, "Installing cua-driver"):
        result = subprocess.run(pip_cmd, capture_output=True, text=True, timeout=300)

    if result.returncode != 0:
        console.print(f"[red]  Failed to install cua-driver: {result.stderr}[/red]")
        console.print("[dim]  You can try later with: halia setup --cua[/dim]")
        return

    # Update config
    from halia.config.settings import read_config, write_config
    config = read_config()
    config["computer_enabled"] = True
    config["computer_backend"] = "cua"
    write_config(config)

    console.print("[green]✓[/green] CUA driver installed and enabled")

    # On macOS, trigger the accessibility permission dialog
    import platform
    if platform.system() == "Darwin":
        console.print(
            "\n[dim]CUA needs Accessibility permission to click and type on your desktop.[/dim]"
        )
        console.print(
            "[dim]A macOS dialog will appear — click 'Allow' or 'OK' to grant access.[/dim]\n"
        )
        # Try to start a CUA session and move the cursor — this triggers the permission dialog
        try:
            import asyncio

            from cua_driver import CuaDriver, StartSessionInput

            async def _request_permission() -> bool:
                driver = CuaDriver.create()
                await driver.start_session(
                    StartSessionInput(session="setup", capture_scope=None, cursor_theme=None)
                )
                # Moving the cursor triggers the accessibility permission dialog
                from cua_driver import DesktopScope, MoveCursorInput
                await driver.move_cursor(
                    MoveCursorInput(
                        session="setup", x=100, y=100,
                        target=None, scope=DesktopScope.DESKTOP,
                    )
                )
                await driver.shutdown()
                return True

            with _Spinner(console, "Requesting accessibility permission"):
                asyncio.run(_request_permission())
            console.print("[green]✓[/green] Accessibility permission granted")
        except Exception as exc:
            console.print(f"[yellow]⚠ Could not verify accessibility permission: {exc}[/yellow]")
            console.print(
                "[dim]  If clicks don't work, manually add cua-driver to:\n"
                "  System Settings → Privacy & Security → Accessibility\n"
                f"  Binary: {_get_cua_binary_path()}[/dim]"
            )

    console.print("[dim]  Use cua_screenshot, cua_click, cua_type to automate desktop.[/dim]")
