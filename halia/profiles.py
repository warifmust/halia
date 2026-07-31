"""Profiles — per-vertical configurations of halia.

A profile bundles the *inputs* to the (fixed) ReAct loop: which skills are
enabled, an optional model, and an optional extra system prompt (persona /
domain guidance). Marketing ≠ finance ≠ compliance.

The trust floor is NOT configurable here: the approval gate, audit trail, and
permission floor live in the loop/skills, and `calculate` is always included by
`build_registry`. A profile can only shape capabilities above that floor.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from halia.store.database import DB_PATH, connect


@dataclass(frozen=True)
class Profile:
    """A named per-vertical configuration."""

    name: str
    skills: list[str]
    model: str | None = None
    extra_prompt: str = ""


def save_profile(profile: Profile, db_path: Path = DB_PATH) -> None:
    """Create or replace a profile."""
    conn = connect(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO profiles (name, skills_json, model, extra_prompt) "
            "VALUES (?, ?, ?, ?)",
            (profile.name, json.dumps(profile.skills), profile.model, profile.extra_prompt),
        )
        conn.commit()
    finally:
        conn.close()


def get_profile(name: str, db_path: Path = DB_PATH) -> Profile | None:
    """Load a profile by name, or None."""
    if not db_path.exists():
        return None
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT name, skills_json, model, extra_prompt FROM profiles WHERE name = ?",
            (name,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    skills: Any = json.loads(row[1])
    return Profile(name=row[0], skills=list(skills), model=row[2], extra_prompt=row[3])


def list_profiles(db_path: Path = DB_PATH) -> list[Profile]:
    """All profiles, by name."""
    if not db_path.exists():
        return []
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name, skills_json, model, extra_prompt FROM profiles ORDER BY name"
        ).fetchall()
    finally:
        conn.close()
    profiles: list[Profile] = []
    for row in rows:
        skills: Any = json.loads(row[1])
        profiles.append(
            Profile(name=row[0], skills=list(skills), model=row[2], extra_prompt=row[3])
        )
    return profiles


def delete_profile(name: str, db_path: Path = DB_PATH) -> bool:
    """Delete a profile; True if it existed."""
    conn = connect(db_path)
    try:
        cursor = conn.execute("DELETE FROM profiles WHERE name = ?", (name,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
