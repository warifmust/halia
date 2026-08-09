"""make_diagram — render a diagram as Mermaid source in a markdown file.

Mermaid is a text DSL for flowcharts, sequence/class/ER/state diagrams, gantt, etc.
The model writes the Mermaid source (it's fluent in it); halia validates the header,
guards the file write, and persists it in a fenced ```mermaid block — which renders
natively on GitHub, VS Code, Obsidian, Claude artifacts, and most markdown viewers.

On-thesis: the diagram IS the code (deterministic, inspectable) — no hallucinated
image. No Node/mermaid-cli dependency; rasterizing to PNG/SVG (via a renderer or the
mermaid.ink API) is a deliberate later opt-in, not bundled here.
"""

from __future__ import annotations

import html as _html
import re
import sqlite3
from pathlib import Path
from typing import Any

from halia.permissions.guard import PermissionDenied, check_readable, check_writable
from halia.skills.db import _column_type, _read_rows, _table_name

_FORMATS = ("md", "mmd", "html")

# Bundled mermaid.js (pinned; see assets/js/MERMAID_VERSION.txt) — lets the html format
# render fully offline in any browser, with nothing fetched at view time.
_MERMAID_JS = Path(__file__).resolve().parent.parent / "assets" / "js" / "mermaid.min.js"


def _load_mermaid_js() -> str | None:
    try:
        return _MERMAID_JS.read_text(encoding="utf-8")
    except OSError:
        return None


def _diagram_html(title: str, code: str, mermaid_js: str) -> str:
    """A self-contained HTML page that renders `code` in-browser via inlined mermaid.js."""
    # Escape so `<`/`&` in the source can't break the page; the browser decodes it back
    # into the <pre>'s textContent, which is exactly what mermaid reads.
    safe_code = _html.escape(code, quote=False)
    heading = f"<h1>{_html.escape(title)}</h1>\n" if title else ""
    page_title = _html.escape(title) if title else "Diagram"
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{page_title}</title>\n"
        "<style>body{font-family:system-ui,-apple-system,sans-serif;margin:2rem;"
        "background:#fff;color:#111}.mermaid{max-width:100%}</style>\n"
        "</head>\n<body>\n"
        f"{heading}"
        f"<pre class=\"mermaid\">\n{safe_code}\n</pre>\n"
        f"<script>{mermaid_js}</script>\n"
        "<script>mermaid.initialize({ startOnLoad: true });</script>\n"
        "</body>\n</html>\n"
    )

# Mermaid diagram-type declaration keywords (the first token of a diagram). Used to
# validate the header and to know when to prepend a `kind`. Not exhaustive — Mermaid
# adds types — so an unknown type is a NOTE, not a hard error.
_KNOWN_TYPES = frozenset({
    "flowchart", "graph", "sequenceDiagram", "classDiagram", "stateDiagram",
    "stateDiagram-v2", "erDiagram", "journey", "gantt", "pie", "mindmap",
    "timeline", "gitGraph", "quadrantChart", "requirementDiagram", "C4Context",
    "C4Container", "C4Component", "sankey-beta", "xychart-beta", "block-beta",
    "packet-beta", "architecture-beta",
})


def _first_token(text: str) -> str:
    stripped = text.strip()
    return stripped.split(None, 1)[0] if stripped else ""


def _declared_type(code: str) -> str | None:
    """The Mermaid type declared on the first meaningful line, if it's a known one."""
    for line in code.splitlines():
        s = line.strip()
        if not s or s.startswith("%%"):  # blank line or Mermaid comment
            continue
        tok = _first_token(s)
        return tok if tok in _KNOWN_TYPES else None
    return None


class MakeDiagram:
    name = "make_diagram"
    description = (
        "Create a diagram from Mermaid source, saved as a markdown file (renders on GitHub, "
        "VS Code, Obsidian, Claude artifacts, and most markdown viewers). For anyone, not just "
        "engineers. Common types: flowchart (processes/decisions), sequenceDiagram (step-by-step "
        "interactions between people or systems), stateDiagram-v2 (lifecycles/statuses), "
        "erDiagram (how data tables relate), journey (user journeys), mindmap (brainstorming), "
        "timeline (chronology of events), quadrantChart (2x2 prioritisation/positioning). Pass "
        "`code` = the Mermaid source (e.g. 'flowchart TD\\n A[Start] --> B[End]'); if it already "
        "starts with the type you can omit `kind`, otherwise pass `kind` and it's prepended. "
        "`format`: 'md' (default, a ```mermaid block that renders on GitHub/VS Code/Obsidian), "
        "'mmd' (raw Mermaid source only — for rendering it yourself), or 'html' (a self-contained "
        "page that renders in any browser, fully offline). "
        "For a chart of NUMERIC data (bar/line/pie of values) use make_chart instead — it renders "
        "exact, tool-computed numbers. To draw an ER diagram from ACTUAL data (a database or "
        "spreadsheets) use make_er_diagram."
    )
    dangerous = True  # writes a file
    untrusted = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "path": {"type": "string", "description": "Output markdown (.md) file path."},
            "code": {"type": "string", "description": "The Mermaid source (no ``` fences)."},
            "kind": {
                "type": "string",
                "description": (
                    "Diagram type (e.g. flowchart, sequenceDiagram, erDiagram). Optional if "
                    "`code` already declares it."
                ),
            },
            "title": {"type": "string", "description": "Optional heading above the diagram."},
            "format": {
                "type": "string",
                "enum": list(_FORMATS),
                "description": (
                    "Output: 'md' (fenced markdown, default), 'mmd' (raw Mermaid source), or "
                    "'html' (self-contained, renders in a browser offline)."
                ),
            },
        },
        "required": ["path", "code"],
    }

    def run(self, args: dict[str, Any]) -> str:
        path = args.get("path")
        code = args.get("code")
        title = str(args.get("title", "")).strip()
        kind = str(args.get("kind", "")).strip()
        if not isinstance(path, str) or not path.strip():
            return "error: 'path' is required"
        if not isinstance(code, str) or not code.strip():
            return "error: 'code' is required — the Mermaid diagram source"
        code = code.strip()
        if "```" in code:
            return "error: 'code' must be the Mermaid source only, without ``` fences"

        # Resolve the diagram type: use the code's own declaration, else prepend `kind`.
        first = _first_token(code)
        if _declared_type(code) is not None:
            effective = first
        elif kind:
            code = f"{kind}\n{code}"
            effective = _first_token(kind)
        else:
            effective = first  # no declaration and no kind — permissive, but flagged below

        fmt = str(args.get("format", "md")).strip().lower() or "md"
        if fmt not in _FORMATS:
            return f"error: 'format' must be one of {', '.join(_FORMATS)}"

        note = ""
        if effective not in _KNOWN_TYPES:
            note = (
                f" (note: '{effective}' isn't a recognized Mermaid diagram type — writing anyway; "
                "it may not render)"
            )

        if fmt == "mmd":
            # Raw Mermaid source only — for rendering it yourself. Title becomes a %% comment.
            content = (f"%% {title}\n{code}\n" if title else f"{code}\n")
        elif fmt == "html":
            mermaid_js = _load_mermaid_js()
            if mermaid_js is None:
                return (
                    "error: bundled mermaid.js not found "
                    f"({_MERMAID_JS}) — reinstall halia, or use format 'md'/'mmd'"
                )
            content = _diagram_html(title, code, mermaid_js)
        else:  # md
            blocks: list[str] = []
            if title:
                blocks.append(f"# {title}\n")
            blocks += ["```mermaid", code, "```"]
            content = "\n".join(blocks) + "\n"

        target = Path(path)
        try:
            check_writable(target)
        except PermissionDenied as exc:
            return f"blocked: {exc}"
        try:
            target.expanduser().write_text(content, encoding="utf-8")
        except OSError as exc:
            return f"error writing {path}: {exc}"
        return f"wrote {effective} diagram ({fmt}) to {path}{note}"


# ── make_er_diagram: an ER diagram GENERATED from real data ─────────────────────
# Unlike make_diagram (which persists model-authored Mermaid), this DERIVES the diagram
# from actual tables — SQLite (real foreign keys = grounded relationships) or CSV/Excel
# (columns grounded; relationships inferred from shared *_id names and flagged).


def _san(name: str) -> str:
    """A Mermaid-safe identifier (letters/digits/underscore)."""
    cleaned = re.sub(r"\W", "_", name).strip("_")
    return cleaned or "x"


def _entity_block(name: str, attrs: list[tuple[str, str, str]]) -> str:
    """One ER entity: `name { <type> <col> <key> … }`. attrs = (type, column, key)."""
    lines = [f"    {_san(name)} {{"]
    for typ, col, key in attrs:
        suffix = f" {key}" if key else ""
        lines.append(f"        {_san(typ) or 'TEXT'} {_san(col)}{suffix}")
    lines.append("    }")
    return "\n".join(lines)


def _sqlite_er(path: Path) -> tuple[list[str], list[str]]:
    """(entity blocks, relationships) from a SQLite DB — using its real PKs and FKs."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]
        blocks: list[str] = []
        rels: list[str] = []
        for t in tables:
            info = conn.execute(f'PRAGMA table_info("{t}")').fetchall()
            fks = conn.execute(f'PRAGMA foreign_key_list("{t}")').fetchall()
            fk_from = {fk[3] for fk in fks}  # 'from' column of each foreign key
            attrs: list[tuple[str, str, str]] = []
            for c in info:  # (cid, name, type, notnull, dflt, pk)
                typ = (c[2] or "TEXT").split("(")[0]
                keys = [k for k, on in (("PK", bool(c[5])), ("FK", c[1] in fk_from)) if on]
                attrs.append((typ, c[1], ",".join(keys)))
            blocks.append(_entity_block(t, attrs))
            for fk in fks:  # (id, seq, table, from, to, …) → parent ||--o{ child
                rels.append(f'    {_san(fk[2])} ||--o{{ {_san(t)} : "{_san(fk[3])}"')
        return blocks, rels
    finally:
        conn.close()


def _files_er(files: list[Path]) -> tuple[list[str], list[str]]:
    """(entity blocks, INFERRED relationships) from CSV/Excel files.

    Columns are read from the files (grounded). A column named `id` is marked PK; a
    relationship is inferred when a `<base>_id` column matches a table named base/bases.
    """
    loaded: list[tuple[str, list[str], list[str]]] = []  # (table, header, types)
    taken: set[str] = set()
    for path in files:
        check_readable(path)
        if not path.is_file():
            raise ValueError(f"not a file: {path}")
        if path.suffix.lower() not in (".csv", ".xlsx", ".xlsm"):
            raise ValueError(f"unsupported file type '{path.suffix}' (use CSV or Excel)")
        rows = _read_rows(path)
        if not rows:
            raise ValueError(f"{path.name} is empty")
        header, body = rows[0], rows[1:]
        types = [
            _column_type([r[c] if c < len(r) else "" for r in body]) for c in range(len(header))
        ]
        loaded.append((_table_name(path.stem, taken), header, types))

    blocks: list[str] = []
    for table, header, types in loaded:
        attrs = [
            (types[i], col, "PK" if col.strip().lower() == "id" else "")
            for i, col in enumerate(header)
        ]
        blocks.append(_entity_block(table, attrs))

    by_name = {t.lower(): t for t, _, _ in loaded}
    rels: list[str] = []
    seen: set[str] = set()
    for table, header, _ in loaded:
        for col in header:
            c = col.strip().lower()
            if not c.endswith("_id") or c == "_id":
                continue
            base = c[:-3]
            parent = next(
                (by_name[n] for n in (base, base + "s", base.rstrip("s")) if n in by_name),
                None,
            )
            if parent and parent != table:  # dashed (..) = inferred, not a real FK
                rel = f'    {_san(parent)} ||..o{{ {_san(table)} : "{_san(col)}"'
                if rel not in seen:
                    seen.add(rel)
                    rels.append(rel)
    return blocks, rels


class MakeErDiagram:
    name = "make_er_diagram"
    description = (
        "Generate an entity-relationship (ER) diagram of how your data tables relate, from ACTUAL "
        "data — a SQLite database (uses its real foreign keys) OR a set of CSV/Excel files "
        "(columns are read from the files; relationships are INFERRED from shared `*_id` column "
        "names and clearly flagged as inferred). Great for seeing the shape of a dataset. Provide "
        "exactly one of `db` or `files`. Saved as a Mermaid ER diagram in a markdown file."
    )
    dangerous = True  # writes a file
    untrusted = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "path": {"type": "string", "description": "Output markdown (.md) file path."},
            "db": {"type": "string", "description": "Path to a SQLite database (real FKs)."},
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "CSV/Excel paths (columns grounded; relationships inferred).",
            },
            "title": {"type": "string", "description": "Optional heading above the diagram."},
        },
        "required": ["path"],
    }

    def run(self, args: dict[str, Any]) -> str:
        path = args.get("path")
        db = args.get("db")
        files = args.get("files")
        title = str(args.get("title", "")).strip()
        if not isinstance(path, str) or not path.strip():
            return "error: 'path' is required"
        if bool(db) == bool(files):
            return "error: provide exactly one of 'db' (a SQLite file) or 'files' (CSV/Excel paths)"

        try:
            if db:
                if not isinstance(db, str):
                    return "error: 'db' must be a path string"
                dbp = Path(db).expanduser()
                check_readable(dbp)
                if not dbp.is_file():
                    return f"error: not a file: {dbp}"
                blocks, rels = _sqlite_er(dbp)
                inferred = False
                source = "database (real FKs)"
            else:
                if not isinstance(files, list) or not files or not all(
                    isinstance(f, str) for f in files
                ):
                    return "error: 'files' must be a non-empty array of file paths"
                blocks, rels = _files_er([Path(f).expanduser() for f in files])
                inferred = True
                source = f"{len(files)} file(s)"
        except PermissionDenied as exc:
            return f"blocked: {exc}"
        except (ValueError, OSError, sqlite3.Error) as exc:
            return f"error: {exc}"

        if not blocks:
            return "error: no tables found to diagram"

        code = "\n".join(["erDiagram", *blocks, *rels])
        parts: list[str] = []
        if title:
            parts.append(f"# {title}\n")
        parts += ["```mermaid", code, "```"]
        if inferred and rels:
            parts.append(
                "\n> Note: columns are read from the files; relationships are *inferred* from "
                "shared `*_id` column names (dashed) — verify them."
            )
        elif inferred:
            parts.append(
                "\n> Note: columns are read from the files; no relationships were inferred "
                "(no matching `*_id` columns)."
            )
        content = "\n".join(parts) + "\n"

        target = Path(path)
        try:
            check_writable(target)
        except PermissionDenied as exc:
            return f"blocked: {exc}"
        try:
            target.expanduser().write_text(content, encoding="utf-8")
        except OSError as exc:
            return f"error writing {path}: {exc}"

        return (
            f"wrote ER diagram — {len(blocks)} tables, {len(rels)} relationships "
            f"from {source} → {path}"
        )

