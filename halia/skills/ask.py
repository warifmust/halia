"""ask_user — pause the run and ask the human operator for something only they have.

The agent runs a whole turn to completion, so without this it can't stop to request a
token, a gated test-data value (a blacklisted user id, a non-eKYC user), a file path, or
a decision — it ends up guessing or silently skipping. `ask_user` blocks, prompts the
person at the terminal, and returns their typed reply so the loop can continue with real
data. Safe (read-only) — it asks, it doesn't act.

Only usable when a human is actually at the terminal; in a non-interactive/scheduled run
it returns an "unavailable" note so the agent flags the case instead of hanging.
"""

from __future__ import annotations

import sys
from typing import Any

from prompt_toolkit import prompt as pt_prompt
from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.styles import Style

_STYLE = Style.from_dict({"prompt": "bold ansigreen"})

# Slash commands available during ask_user.
_SLASH_COMMANDS = [
    ("/quit", "exit halia"),
    ("/exit", "exit halia"),
    ("/local", "toggle local egress (on/off)"),
    ("/compact", "compact context"),
    ("/help", "show help"),
]


class _SlashCompleter(Completer):
    """Show slash commands when user types `/`."""

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> list[Completion]:
        text = document.text_before_cursor
        if not text.startswith("/"):
            return []
        # Allow partial matches: "/q" matches "/quit", "/lo" matches "/local"
        return [
            Completion(cmd, start_position=-len(text), display=cmd, display_meta=desc)
            for cmd, desc in _SLASH_COMMANDS
            if cmd.startswith(text)
        ]


class AskUser:
    name = "ask_user"
    description = (
        "Ask the human operator for information only they can provide — a token or "
        "credential, a gated test-data value (e.g. a blacklisted user id, a non-eKYC user), "
        "a file path, or a decision — and get their typed reply. Use this instead of "
        "inventing gated/secret values or silently skipping a step for lack of data. Set "
        "secret=true to hide the typed input (tokens/passwords)."
    )
    dangerous = False
    untrusted = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "question": {"type": "string", "description": "What to ask the user."},
            "secret": {
                "type": "boolean",
                "description": "Hide the typed input as it's entered (tokens/passwords).",
            },
        },
        "required": ["question"],
    }

    def run(self, args: dict[str, Any]) -> str:
        question = args.get("question")
        if not isinstance(question, str) or not question.strip():
            return "error: 'question' is required and must be a non-empty string"
        if not sys.stdin.isatty():
            return (
                "unavailable: no interactive user to ask (non-interactive run) — "
                "skip this step or flag it for a human."
            )

        print(f"\n{question.strip()}")
        try:
            answer = pt_prompt(
                [("class:prompt", "❯ ")],
                is_password=bool(args.get("secret")),
                style=_STYLE,
                completer=_SlashCompleter(),
                complete_while_typing=True,
                complete_in_thread=True,
            )
        except (EOFError, KeyboardInterrupt):
            return "no answer (user cancelled) — skip this step or flag it for a human."

        answer = answer.strip()
        if not answer:
            return "user gave no answer — treat this as 'skip this step'."
        if answer.lower() in ("skip", "none", "n/a"):
            return "user chose to skip this step."
        # Slash commands typed during ask_user — handle them, don't pass to model.
        if answer.startswith("/"):
            cmd = answer.split()[0].lower()
            if cmd in ("/quit", "/exit"):
                import sys as _sys
                _sys.exit(0)
            if cmd == "/local":
                from halia.permissions.network import set_allow_local
                parts = answer.split()
                if len(parts) >= 2 and parts[1].lower() in ("on", "off"):
                    set_allow_local(parts[1].lower() == "on")
                else:
                    from halia.permissions.network import allow_local_enabled
                    set_allow_local(not allow_local_enabled())
                state = "ON" if allow_local_enabled() else "OFF"
                return f"local egress {state} — continuing with the task."
            if cmd == "/compact":
                return "user requested compaction — continue the task."
            if cmd == "/help":
                return "user requested help — continue the task."
            # Unknown slash command — pass through as answer.
        return f"user answered: {answer}"
