"""PDF ingestion — read_pdf (text-based).

Extracts text from a text-based PDF via pypdf (lean, pure-Python — no Pillow).
Scanned/image PDFs have no extractable text and would need OCR, which is a
separate capability (an OCR provider), deliberately not bundled here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from halia.permissions.guard import check_readable

_MAX_CHARS = 10_000


class ReadPdf:
    name = "read_pdf"
    description = (
        "Read a TEXT-based PDF and return its extracted text (truncated). "
        "Scanned/image PDFs have no extractable text and would need OCR (not supported)."
    )
    dangerous = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "path": {"type": "string", "description": "Path to the .pdf file."},
            "max_chars": {
                "type": "integer",
                "description": "Max characters of text to return (default 10000).",
            },
        },
        "required": ["path"],
    }

    def run(self, args: dict[str, Any]) -> str:
        raw = args.get("path")
        if not isinstance(raw, str) or not raw.strip():
            return "error: 'path' is required and must be a non-empty string"
        path = Path(raw).expanduser()
        check_readable(path)
        if not path.is_file():
            return f"error: not a file: {path}"

        max_chars = args.get("max_chars", _MAX_CHARS)
        if not isinstance(max_chars, int) or max_chars <= 0:
            max_chars = _MAX_CHARS

        num_pages = 0
        parts: list[str] = []
        total = 0
        try:
            reader = PdfReader(str(path))
            num_pages = len(reader.pages)
            for i, page in enumerate(reader.pages):
                text = page.extract_text() or ""
                if text.strip():
                    parts.append(f"[page {i + 1}]\n{text}")
                    total += len(text)
                if total >= max_chars:
                    break
        except (PdfReadError, OSError, ValueError) as exc:
            return f"error reading PDF: {exc}"

        combined = "\n\n".join(parts)
        if not combined.strip():
            return (
                "no extractable text found — this PDF may be scanned/image-based, "
                "which needs OCR (not yet supported)."
            )
        if len(combined) > max_chars:
            combined = combined[:max_chars] + "\n… [truncated]"
        return f"{num_pages} page(s). extracted text:\n\n{combined}"
