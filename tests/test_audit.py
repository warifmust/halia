"""Tests for the durable audit trail and the trace preview."""

from typing import Any

from halia.audit.record import list_runs, new_record, save_run
from halia.audit.trace import Step


def test_save_and_list_roundtrip(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    record = new_record(
        "deepseek", "deepseek-v4-flash", "hi", "hello", [Step("list_files", "{}", "a\nb")]
    )
    save_run(record, db_path=db)

    loaded = list_runs(db_path=db)
    assert len(loaded) == 1
    assert loaded[0].id == record.id
    assert loaded[0].prompt == "hi"
    assert loaded[0].answer == "hello"
    assert loaded[0].steps[0].tool == "list_files"
    assert loaded[0].steps[0].observation == "a\nb"


def test_list_runs_limit_respected(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    for prompt in ("first", "second", "third"):
        save_run(new_record("p", "m", prompt, "a", []), db_path=db)
    assert len(list_runs(db_path=db, limit=2)) == 2


def test_list_runs_empty(tmp_path: Any) -> None:
    assert list_runs(db_path=tmp_path / "nope.db") == []


def test_step_preview_truncates() -> None:
    step = Step("t", "{}", "x" * 500)
    preview = step.preview(50)
    assert preview.endswith("…")
    assert len(preview) <= 51
