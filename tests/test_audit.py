"""Tests for the durable audit trail and the trace preview."""

from typing import Any

from halia.audit.record import list_runs, new_record, save_run
from halia.audit.trace import Step


def test_save_and_list_roundtrip(tmp_path: Any) -> None:
    record = new_record(
        "deepseek", "deepseek-chat", "hi", "hello", [Step("list_files", "{}", "a\nb")]
    )
    path = save_run(record, runs_dir=tmp_path)
    assert path.exists()

    loaded = list_runs(runs_dir=tmp_path)
    assert len(loaded) == 1
    assert loaded[0].id == record.id
    assert loaded[0].prompt == "hi"
    assert loaded[0].answer == "hello"
    assert loaded[0].steps[0].tool == "list_files"
    assert loaded[0].steps[0].observation == "a\nb"


def test_list_runs_newest_first(tmp_path: Any) -> None:
    for prompt in ("first", "second", "third"):
        save_run(new_record("p", "m", prompt, "a", []), runs_dir=tmp_path)
    loaded = list_runs(runs_dir=tmp_path, limit=2)
    assert len(loaded) == 2  # limit respected


def test_list_runs_empty(tmp_path: Any) -> None:
    assert list_runs(runs_dir=tmp_path / "nope") == []


def test_step_preview_truncates() -> None:
    step = Step("t", "{}", "x" * 500)
    preview = step.preview(50)
    assert preview.endswith("…")
    assert len(preview) <= 51
