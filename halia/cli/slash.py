"""Pure helpers for chat slash commands.

These do the logic (no console, no I/O) so they're testable and shared verbatim by
both surfaces — `halia chat` (cli/main.py) and the TUI (cli/tui.py). The thin
console-printing dispatchers (`_chat_history`, `_chat_cost`, …) live in cli/main.py.
"""

from __future__ import annotations

from typing import Any

from halia.providers.base import Message


def _text_of(message: Message) -> str:
    """A message's content as plain text (image/multimodal turns collapse to a note)."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # multimodal: pull the text parts out
        parts = [
            p.get("text", "")
            for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        ]
        joined = " ".join(t for t in parts if t)
        return joined + (" [+image]" if len(parts) < len(content) else "")
    return ""


def format_history(messages: list[Message], n: int = 10) -> str:
    """The last `n` user/assistant messages, one per line, truncated (n<=0 = all)."""
    convo = [m for m in messages if m.get("role") in ("user", "assistant")]
    if not convo:
        return "(no conversation yet)"
    shown = convo[-n:] if n > 0 else convo
    lines: list[str] = []
    for m in shown:
        label = "you" if m.get("role") == "user" else "halia"
        text = " ".join(_text_of(m).split())
        if len(text) > 200:
            text = text[:200] + "…"
        lines.append(f"{label}: {text}")
    return "\n".join(lines)


def conversation_markdown(
    messages: list[Message], title: str = "halia conversation", meta: dict[str, Any] | None = None
) -> str:
    """Render the user/assistant transcript as markdown (system + tool turns omitted)."""
    out: list[str] = [f"# {title}", ""]
    if meta:
        out += [f"- **{k}**: {v}" for k, v in meta.items()]
        out.append("")
    for m in messages:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        heading = "You" if role == "user" else "halia"
        out += [f"## {heading}", "", _text_of(m), ""]
    return "\n".join(out).rstrip() + "\n"


def drop_last_exchange(messages: list[Message]) -> int:
    """Remove the most recent user message and everything after it. Returns count removed.

    Keeps the leading system prompt. This only edits the conversation — it does NOT
    undo any file writes or other side effects tools already performed.
    """
    last_user = next(
        (i for i in range(len(messages) - 1, -1, -1) if messages[i].get("role") == "user"),
        None,
    )
    if last_user is None or last_user == 0:
        return 0
    removed = len(messages) - last_user
    del messages[last_user:]
    return removed


def human_count(n: int) -> str:
    """Abbreviate a token count for compact display: 842, 9.6k, 19.2k, 100k, 1.2M."""
    if n < 0:
        return str(n)
    for div, suffix in ((1_000_000, "M"), (1_000, "k")):
        if n >= div:
            s = f"{n / div:.1f}".rstrip("0").rstrip(".")
            return f"{s}{suffix}"
    return str(n)


def available_models(provider: str) -> list[str]:
    """The curated model list for a provider (minus the 'Custom model…' picker sentinel)."""
    from halia.config.settings import PROVIDERS

    spec = PROVIDERS.get(provider)
    if spec is None:
        return []
    return [m for m in spec.models if m != "Custom model…"]
