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

        print(f"\n\033[1;36m[halia needs input]\033[0m {question.strip()}")
        try:
            if bool(args.get("secret")):
                import getpass

                answer = getpass.getpass("  (hidden) › ")
            else:
                answer = input("  › ")
        except (EOFError, KeyboardInterrupt):
            return "no answer (user cancelled) — skip this step or flag it for a human."

        answer = answer.strip()
        if not answer:
            return "user gave no answer — treat this as 'skip this step'."
        if answer.lower() in ("skip", "none", "n/a"):
            return "user chose to skip this step."
        return f"user answered: {answer}"
