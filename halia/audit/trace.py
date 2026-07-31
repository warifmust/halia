"""Provenance — the structured record of what a run actually did.

Trust products show their work. Every tool the agent runs becomes a `Step`
(what was called, with what arguments, and what came back), so a run is
inspectable and auditable rather than an opaque final answer.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Step:
    """One tool execution in a run."""

    tool: str
    arguments: str
    observation: str

    def preview(self, limit: int = 200) -> str:
        """A short, single-line preview of the observation for live display."""
        text = " ".join(self.observation.split())
        return text if len(text) <= limit else text[:limit] + "…"
