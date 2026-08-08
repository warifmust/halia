"""learn_from_reference — read taught files and extract format instructions.

Before starting a task, the model calls this skill to read any files the user
has taught via `/teach`. The skill extracts the content and returns it so the
model can follow the taught format.

The system prompt tells the model to call this at the start of each task.
Files are filtered by the current profile — if running in "qa" mode, only
qa-tagged files are loaded.
"""

from __future__ import annotations

from typing import Any

from halia.config.settings import CONFIG_DIR

_FILES_DIR = CONFIG_DIR / "files"
_MAX_CONTENT = 8000  # chars per file to avoid blowing context


class LearnFromReference:
    name = "learn_from_reference"
    description = (
        "Read files the user has taught via /teach and extract their format/rules. "
        "Call this at the start of a task to learn the required format. Files are "
        "filtered by the current profile (qa, finance, etc.) — only relevant files "
        "are loaded. Returns the content of each taught file."
    )
    dangerous = False
    untrusted = False  # these are user-taught, trusted files
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "profile": {
                "type": "string",
                "description": "Filter by profile (qa, finance, etc.). Empty = all files.",
            },
            "filename": {
                "type": "string",
                "description": "Specific filename to read (optional, default: all matching files).",
            },
        },
    }

    def run(self, args: dict[str, Any]) -> str:
        profile = args.get("profile", "")
        filename = args.get("filename", "")

        from halia.references import get_reference_path, list_ref_files

        refs = list_ref_files(profile=profile if profile else None)
        if not refs:
            if profile:
                return f"no reference files taught for profile '{profile}'. Use /teach to add some."
            return "no reference files taught yet. Use /teach to add some."

        if filename:
            refs = [r for r in refs if r.filename == filename]
            if not refs:
                return f"no reference file found matching '{filename}'"

        results: list[str] = []
        for ref in refs:
            path = get_reference_path(ref.id)
            if path is None:
                results.append(f"[{ref.filename}] — file missing from storage")
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                results.append(f"[{ref.filename}] — could not read file")
                continue

            if len(content) > _MAX_CONTENT:
                content = content[:_MAX_CONTENT] + "\n… (truncated)"

            tag = f" [{ref.profile}]" if ref.profile else ""
            header = f"=== {ref.filename}{tag} ({ref.file_type}) ==="
            if ref.description:
                header += f"\n{ref.description}"
            results.append(f"{header}\n{content}")

        if not results:
            return "no readable reference files found."

        return "\n\n".join(results)
