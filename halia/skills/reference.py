"""learn_from_reference — read taught files and extract format instructions.

Before starting a task, the model calls this skill to read any files the user
has taught via `/teach`. The skill extracts the content and returns it so the
model can follow the taught format.

The system prompt tells the model to call this at the start of each task.
Files are filtered by the current profile — if running in "qa" mode, only
qa-tagged files are loaded.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from halia.config.settings import CONFIG_DIR

_FILES_DIR = CONFIG_DIR / "files"
_MAX_CONTENT = 8000  # chars per file to avoid blowing context


def _read_reference_content(path: Path, file_type: str) -> str:
    """Extract a taught file's text by type.

    Binary office formats (.pdf/.docx/.xlsx) are routed through the proper extractors —
    reading them as raw text would return mojibake. Text formats are read directly.
    The stored file lives under ~/.halia/files/ (halia owns it), so no floor check.
    """
    ext = file_type.lower()
    try:
        if ext == ".pdf":
            from halia.skills.pdf import extract_pdf_text

            return extract_pdf_text(path, _MAX_CONTENT)
        if ext == ".docx":
            from halia.skills.word import extract_docx_text

            return extract_docx_text(path, _MAX_CONTENT)
        if ext == ".xlsx":
            from halia.skills.excel import extract_excel_text

            return extract_excel_text(path, max_chars=_MAX_CONTENT)
        content = path.read_text(encoding="utf-8", errors="replace")
        if len(content) > _MAX_CONTENT:
            content = content[:_MAX_CONTENT] + "\n… (truncated)"
        return content
    except Exception as exc:  # noqa: BLE001 — a bad taught file is a note, not a crash
        return f"(could not extract content: {exc})"


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

            content = _read_reference_content(path, ref.file_type)

            tag = f" [{ref.profile}]" if ref.profile else ""
            header = f"=== {ref.filename}{tag} ({ref.file_type}) ==="
            if ref.url:
                header += f"\nsource: {ref.url}  (cite this URL when you use it)"
            if ref.description:
                header += f"\n{ref.description}"
            results.append(f"{header}\n{content}")

        if not results:
            return "no readable reference files found."

        return "\n\n".join(results)


class SaveReference:
    name = "save_reference"
    description = (
        "Remember a document, file, or URL as a reusable reference to use going forward — when "
        "the user says things like 'remember this OpenAPI spec', 'use this doc for tests', or "
        "'keep this for later'. Saves it so learn_from_reference can load it in future runs. "
        "`source` = a local file path OR an http(s) URL; optional `profile` to scope it "
        "(qa/finance/…) and `description`. Confirm what you'll save with the user first — never "
        "save silently."
    )
    dangerous = True  # the approval gate IS the confirmation (mirrors save_procedure)
    untrusted = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "source": {"type": "string", "description": "A local file path or an http(s) URL."},
            "profile": {"type": "string", "description": "Optional profile scope (qa, finance…)."},
            "description": {"type": "string", "description": "Optional note on what it teaches."},
        },
        "required": ["source"],
    }

    def run(self, args: dict[str, Any]) -> str:
        import httpx

        from halia.permissions.network import EgressDenied
        from halia.references import store_reference, store_url_reference

        source = args.get("source")
        if not isinstance(source, str) or not source.strip():
            return "error: 'source' is required (a local file path or an http(s) URL)"
        source = source.strip()
        profile = str(args.get("profile", "")).strip()
        description = str(args.get("description", "")).strip()
        tag = f" [{profile}]" if profile else ""
        try:
            if source.startswith(("http://", "https://")):
                ref = store_url_reference(source, profile=profile, description=description)
                return (
                    f"remembered URL reference '{ref.filename}'{tag} (from {ref.url}). "
                    "Load it later with learn_from_reference."
                )
            ref = store_reference(source, profile=profile, description=description)
            return (
                f"remembered reference '{ref.filename}'{tag} ({ref.file_type}). "
                "Load it later with learn_from_reference."
            )
        except (FileNotFoundError, ValueError, OSError, EgressDenied, httpx.HTTPError) as exc:
            return f"error: {exc}"
