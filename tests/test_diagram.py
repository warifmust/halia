"""Tests for the make_diagram skill (Mermaid → markdown file)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from halia.skills.diagram import MakeDiagram, MakeErDiagram


def _run(args: dict[str, object]) -> str:
    return MakeDiagram().run(args)


def _er(args: dict[str, object]) -> str:
    return MakeErDiagram().run(args)


def test_writes_mermaid_block_from_declared_code(tmp_path: Path) -> None:
    out = tmp_path / "flow.md"
    res = _run({"path": str(out), "code": "flowchart TD\n  A[Start] --> B[End]"})
    assert res.startswith("wrote flowchart diagram")
    body = out.read_text()
    assert "```mermaid" in body and "flowchart TD" in body and body.rstrip().endswith("```")


def test_prepends_kind_when_code_lacks_declaration(tmp_path: Path) -> None:
    out = tmp_path / "seq.md"
    res = _run({"path": str(out), "kind": "sequenceDiagram", "code": "Alice->>Bob: hi"})
    assert "wrote sequenceDiagram diagram" in res
    body = out.read_text()
    # the type declaration is prepended exactly once, above the body
    assert body.count("sequenceDiagram") == 1
    assert body.index("sequenceDiagram") < body.index("Alice->>Bob")


def test_does_not_double_declare_when_kind_matches_code(tmp_path: Path) -> None:
    out = tmp_path / "flow.md"
    _run({"path": str(out), "kind": "flowchart", "code": "flowchart LR\n A --> B"})
    assert out.read_text().count("flowchart") == 1  # code's own declaration wins


def test_title_becomes_heading(tmp_path: Path) -> None:
    out = tmp_path / "d.md"
    _run({"path": str(out), "title": "Login Flow", "code": "flowchart TD\n A --> B"})
    body = out.read_text()
    assert body.startswith("# Login Flow")
    assert body.index("# Login Flow") < body.index("```mermaid")


def test_rejects_empty_code(tmp_path: Path) -> None:
    assert _run({"path": str(tmp_path / "x.md"), "code": "   "}).startswith("error")


def test_rejects_fenced_code(tmp_path: Path) -> None:
    res = _run({"path": str(tmp_path / "x.md"), "code": "```mermaid\nflowchart TD\nA-->B\n```"})
    assert res.startswith("error") and "```" in res


def test_unknown_type_writes_with_note(tmp_path: Path) -> None:
    out = tmp_path / "x.md"
    res = _run({"path": str(out), "code": "notADiagram foo\n  bar"})
    assert res.startswith("wrote") and "isn't a recognized Mermaid" in res
    assert out.exists()  # permissive: still written


def test_blocks_sensitive_path(tmp_path: Path) -> None:
    # the fs floor denies .ssh/.aws/.gnupg/.halia
    res = _run({"path": str(tmp_path / ".ssh" / "d.md"), "code": "flowchart TD\n A --> B"})
    assert res.startswith("blocked")


def test_format_mmd_is_raw_source(tmp_path: Path) -> None:
    out = tmp_path / "d.mmd"
    res = _run({"path": str(out), "format": "mmd", "code": "flowchart TD\n A --> B", "title": "T"})
    assert "(mmd)" in res
    body = out.read_text()
    assert "```" not in body  # no markdown fence
    assert "<pre" not in body  # not html
    assert "flowchart TD" in body
    assert body.startswith("%% T")  # title becomes a Mermaid comment


def test_format_html_self_contained_and_escaped(tmp_path: Path) -> None:
    out = tmp_path / "d.html"
    res = _run({"path": str(out), "format": "html", "code": "flowchart TD\n A[x < y] --> B"})
    assert "(html)" in res
    body = out.read_text()
    assert body.lstrip().startswith("<!doctype html")
    assert '<pre class="mermaid">' in body
    assert "mermaid.initialize" in body
    assert len(body) > 1_000_000  # mermaid.js is inlined (offline, self-contained)
    assert "x &lt; y" in body  # source is HTML-escaped so `<` can't break the page
    assert "--&gt;" in body


def test_invalid_format_errors(tmp_path: Path) -> None:
    res = _run({"path": str(tmp_path / "d.png"), "format": "png", "code": "flowchart TD\n A --> B"})
    assert res.startswith("error")


def test_registered_in_catalogue_and_generalist() -> None:
    from halia.skills import DEFAULT_SKILLS, available_skills

    assert "make_diagram" in available_skills()
    assert "make_diagram" in DEFAULT_SKILLS  # auto-joins the no-profile generalist
    assert "make_er_diagram" in available_skills()
    assert "make_er_diagram" in DEFAULT_SKILLS


# ── make_er_diagram ─────────────────────────────────────────────────────────────


def test_er_from_sqlite_uses_real_foreign_keys(tmp_path: Path) -> None:
    db = tmp_path / "shop.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT);"
        "CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER, total REAL,"
        " FOREIGN KEY (customer_id) REFERENCES customers(id));"
    )
    conn.commit()
    conn.close()

    out = tmp_path / "er.md"
    res = _er({"path": str(out), "db": str(db)})
    assert res.startswith("wrote ER diagram")
    body = out.read_text()
    assert "erDiagram" in body
    assert "customers {" in body and "orders {" in body
    assert "id PK" in body  # primary key marked
    assert "customer_id FK" in body  # foreign key marked
    assert "customers ||--o{ orders" in body  # SOLID = grounded real FK


def test_er_from_csv_infers_relationships_and_flags_them(tmp_path: Path) -> None:
    (tmp_path / "customers.csv").write_text("id,name\n1,Alice\n2,Bob\n")
    (tmp_path / "orders.csv").write_text("id,customer_id,total\n1,1,50\n2,2,75\n")
    out = tmp_path / "er.md"
    res = _er({
        "path": str(out),
        "files": [str(tmp_path / "customers.csv"), str(tmp_path / "orders.csv")],
    })
    assert res.startswith("wrote ER diagram")
    body = out.read_text()
    assert "customers ||..o{ orders" in body  # DASHED = inferred
    assert "inferred" in body.lower()  # flagged, not asserted
    assert "id PK" in body


def test_er_requires_exactly_one_source(tmp_path: Path) -> None:
    p = str(tmp_path / "er.md")
    assert _er({"path": p}).startswith("error")  # neither
    assert _er({"path": p, "db": "x.db", "files": ["y.csv"]}).startswith("error")  # both


def test_er_blocks_sensitive_output(tmp_path: Path) -> None:
    (tmp_path / "t.csv").write_text("id,name\n1,A\n")
    res = _er({"path": str(tmp_path / ".ssh" / "er.md"), "files": [str(tmp_path / "t.csv")]})
    assert res.startswith("blocked")
