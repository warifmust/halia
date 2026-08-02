"""Curated persona presets — profiles that ship *in the box*.

A preset is nothing new architecturally: it is a `Profile` (§profiles) that halia
ships with, so a user can run `halia finance …` and land in the right posture
(skills + persona + limits) without authoring a profile from scratch. Presets are
the *front door* to the profile system, not a replacement for the general engine —
`halia run` with no persona stays the horizontal default.

IMPORTANT distinction: a persona (this prompt + skill subset) is cheap and cosmetic.
The trust differentiation lives one layer deeper in the per-domain *verification*
(reconciliation, number-grounding, audit) — which is horizontal and always on. A
preset just points the general engine at a vertical; it does not, by itself, make
the output trustworthy. See the requirements doc, §8 "Curated persona presets".
"""

from __future__ import annotations

from halia.profiles import Profile, get_profile

_FINANCE_PROMPT = (
    "You are halia in FINANCE mode — a meticulous finance assistant for ledgers, "
    "statements, reconciliations, and planning. Every figure you report MUST come "
    "from a tool result, never from mental arithmetic: use aggregate_csv to total or "
    "average a column, reconcile_csv to compare two sources, and calculate for any "
    "other arithmetic. When you present numbers, say where each one came from (which "
    "file, which tool). Money is exact — treat amounts as precise decimals, never "
    "round silently. If a figure cannot be grounded in a source, say so rather than "
    "assert it."
)

_RESEARCH_PROMPT = (
    "You are halia in RESEARCH mode — a careful research companion. Gather information "
    "using tools: web_search to DISCOVER sources, fetch_url to read a promising result, "
    "read_pdf / read_file for documents. Base your answer on what you ACTUALLY retrieved. "
    "Cite your sources: after a claim, note "
    "where it came from — the URL or the file. Distinguish established fact from your own "
    "inference or synthesis, and flag what you are uncertain about or could not verify. "
    "NEVER fabricate a source, quote, statistic, or citation — if you did not retrieve it, "
    "say so plainly. Prefer primary sources, and note the date or recency of what you find."
)

# Built-in presets, keyed by the name a user invokes (`halia finance …`, `halia research …`).
# A user profile saved under the same name in the DB overrides the built-in (customization).
BUILTIN_PRESETS: dict[str, Profile] = {
    "finance": Profile(
        name="finance",
        skills=[
            "read_file",
            "list_files",
            "read_csv",
            "aggregate_csv",
            "reconcile_csv",
            "read_excel",
            "read_pdf",
            "query_db",
            "write_file",  # for report deliverables; still approval-gated
        ],
        model=None,
        extra_prompt=_FINANCE_PROMPT,
    ),
    "research": Profile(
        name="research",
        skills=[
            "web_search",  # discover sources
            "fetch_url",  # then retrieve them
            "read_file",
            "read_pdf",
            "list_files",
            "read_csv",
            "aggregate_csv",
            "write_file",  # for saving notes / a research brief; approval-gated
        ],
        model=None,
        extra_prompt=_RESEARCH_PROMPT,
    ),
}


def get_preset(name: str) -> Profile | None:
    """Return the built-in preset for `name`, or None."""
    return BUILTIN_PRESETS.get(name)


def preset_names() -> list[str]:
    """Names of all built-in presets."""
    return sorted(BUILTIN_PRESETS)


def resolve_profile(name: str) -> Profile | None:
    """Resolve a profile by name: a user's saved profile wins, else the built-in preset.

    DB-first so a user can customize a shipped preset (save a profile of the same
    name and it takes over); falls back to the built-in for the out-of-the-box path.
    """
    return get_profile(name) or get_preset(name)
