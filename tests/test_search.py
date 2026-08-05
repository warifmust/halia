"""Tests for search_code — the find-references / repo-grep skill."""

from typing import Any

from halia.skills.search import SearchCode


def _tree(tmp_path: Any) -> Any:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "service.ts").write_text(
        "class S {\n"
        "  handle(x: Dto) {\n"
        "    if (x.verificationPassed) return this.amend();\n"
        "    return this.reject();\n"
        "  }\n"
        "}\n"
    )
    (tmp_path / "src" / "dto.ts").write_text(
        "export class Dto {\n"
        "  @IsBoolean() opsJudgement: boolean;  // required but never read\n"
        "  @IsBoolean() verificationPassed: boolean;\n"
        "}\n"
    )
    # noise that must be skipped
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.ts").write_text("opsJudgement everywhere\n")
    return tmp_path


def test_finds_symbol_with_file_and_line(tmp_path: Any) -> None:
    _tree(tmp_path)
    out = SearchCode().run({"query": "verificationPassed", "path": str(tmp_path)})
    assert "service.ts:3:" in out
    assert "dto.ts:3:" in out
    assert "2 match(es)" in out or "2+ match" in out


def test_skips_node_modules(tmp_path: Any) -> None:
    _tree(tmp_path)
    out = SearchCode().run({"query": "opsJudgement", "path": str(tmp_path)})
    # only the DTO declaration — the logic never reads it, and node_modules is skipped
    assert "dto.ts:2:" in out
    assert "node_modules" not in out
    assert "1 match(es)" in out


def test_no_match_is_explicit(tmp_path: Any) -> None:
    _tree(tmp_path)
    assert "No matches" in SearchCode().run({"query": "nonexistentSymbol", "path": str(tmp_path)})


def test_file_glob_limits_extensions(tmp_path: Any) -> None:
    _tree(tmp_path)
    (tmp_path / "src" / "notes.md").write_text("verificationPassed mentioned in docs\n")
    out = SearchCode().run(
        {"query": "verificationPassed", "path": str(tmp_path), "file_glob": "*.ts"}
    )
    assert "notes.md" not in out
    assert "service.ts" in out


def test_regex_mode(tmp_path: Any) -> None:
    _tree(tmp_path)
    out = SearchCode().run({"query": r"return this\.\w+\(\)", "path": str(tmp_path), "regex": True})
    assert "amend" in out and "reject" in out


def test_invalid_regex_is_an_error() -> None:
    assert "invalid regex" in SearchCode().run({"query": "(unclosed", "regex": True})


def test_missing_query_is_an_error() -> None:
    assert "query" in SearchCode().run({"query": "  "})
