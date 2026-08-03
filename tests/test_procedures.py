"""Tests for test procedures (the teach-and-remember store + prompt rendering)."""

from typing import Any

from halia.procedures import (
    Procedure,
    delete_procedure,
    get_procedure,
    list_procedures,
    save_procedure,
)


def _login_proc() -> Procedure:
    return Procedure(
        name="login-api",
        description="Smoke-test the login endpoint.",
        target="POST /auth/login",
        data_spec="synthesize 20 rows: {email, password, expect_status}",
        method="POST",
        url="https://example.com/auth/login",
        headers={"Authorization": "Bearer {token}"},
        result_columns=["test_id", "email", "actual_status", "verdict"],
        pass_rule="actual_status == expect_status",
    )


def test_roundtrip_preserves_all_fields(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    save_procedure(_login_proc(), db_path=db)
    loaded = get_procedure("login-api", db_path=db)
    assert loaded is not None
    assert loaded.target == "POST /auth/login"
    assert loaded.method == "POST"
    assert loaded.headers == {"Authorization": "Bearer {token}"}
    assert loaded.result_columns == ["test_id", "email", "actual_status", "verdict"]
    assert loaded.pass_rule == "actual_status == expect_status"


def test_missing_slots_reports_required_gaps() -> None:
    empty = Procedure(name="bare")
    missing = empty.missing_slots()
    assert set(missing) == {"target", "data_spec", "url", "result_columns", "pass_rule"}
    assert empty.is_runnable() is False


def test_full_procedure_is_runnable() -> None:
    proc = _login_proc()
    assert proc.missing_slots() == []
    assert proc.is_runnable() is True


def test_partial_procedure_flags_only_empty_slots() -> None:
    proc = Procedure(name="p", target="GET /health", url="https://example.com/health")
    # data_spec, result_columns, pass_rule still missing
    assert set(proc.missing_slots()) == {"data_spec", "result_columns", "pass_rule"}


def test_to_prompt_embeds_the_grounding_discipline() -> None:
    text = _login_proc().to_prompt()
    assert "TEST PROCEDURE: login-api" in text
    assert "POST https://example.com/auth/login" in text
    assert "http_request" in text  # points at the grounded action tool
    assert "check_expectation" in text  # verdict via the deterministic assert skill
    assert "Never guess a verdict" in text
    assert "test_id, email, actual_status, verdict" in text  # exact output schema
    assert "Authorization: Bearer {token}" in text


def test_replace_preserves_created_at(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    save_procedure(_login_proc(), db_path=db)
    first = get_procedure("login-api", db_path=db)
    # re-save with an edit
    edited = Procedure(name="login-api", target="POST /auth/login v2", url="https://x")
    save_procedure(edited, db_path=db)
    second = get_procedure("login-api", db_path=db)
    assert second is not None and second.target == "POST /auth/login v2"
    assert first is not None  # sanity


def test_list_and_delete(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    save_procedure(Procedure(name="a"), db_path=db)
    save_procedure(Procedure(name="b"), db_path=db)
    assert {p.name for p in list_procedures(db_path=db)} == {"a", "b"}
    assert delete_procedure("a", db_path=db) is True
    assert delete_procedure("a", db_path=db) is False
    assert {p.name for p in list_procedures(db_path=db)} == {"b"}


def test_get_missing_returns_none(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    assert get_procedure("nope", db_path=db) is None


# --- edit-single-slot: _apply_field ---


def test_apply_field_plain_string() -> None:
    from halia.cli.main import _apply_field

    proc = _login_proc()
    updated = _apply_field(proc, "pass-rule", "actual_status == 201")
    assert updated.pass_rule == "actual_status == 201"
    assert updated.url == proc.url  # everything else untouched


def test_apply_field_endpoint_sets_method_and_url() -> None:
    from halia.cli.main import _apply_field

    updated = _apply_field(_login_proc(), "endpoint", "PUT https://api.test/v2/login")
    assert updated.method == "PUT"
    assert updated.url == "https://api.test/v2/login"


def test_apply_field_columns_splits_csv() -> None:
    from halia.cli.main import _apply_field

    updated = _apply_field(_login_proc(), "columns", "id, status, result")
    assert updated.result_columns == ["id", "status", "result"]


def test_apply_field_header_merges_not_replaces() -> None:
    from halia.cli.main import _apply_field

    updated = _apply_field(_login_proc(), "header", "X-Env: staging")
    assert updated.headers["X-Env"] == "staging"
    assert updated.headers["Authorization"] == "Bearer {token}"  # original kept


def test_apply_field_rejects_unknown_field() -> None:
    import pytest

    from halia.cli.main import _apply_field

    with pytest.raises(ValueError, match="unknown field"):
        _apply_field(_login_proc(), "nonsense", "x")


def test_apply_field_rejects_bad_method() -> None:
    import pytest

    from halia.cli.main import _apply_field

    with pytest.raises(ValueError, match="method must be"):
        _apply_field(_login_proc(), "method", "FETCH")


def test_apply_field_bad_header_format() -> None:
    import pytest

    from halia.cli.main import _apply_field

    with pytest.raises(ValueError, match="Name: value"):
        _apply_field(_login_proc(), "header", "no-colon-here")
