"""Tests for the durable audit trail and the trace preview."""

from typing import Any

from halia.audit.record import RunRecord, get_run, list_runs, new_record, save_run
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


def test_trust_receipts_persist(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    record = new_record(
        "p", "m", "reconcile", "done",
        [Step("reconcile_csv", "{}", "matched: 3")],
        plan="1. reconcile\n2. total",
        unverified=["730.50"],
        corrections=1,
    )
    save_run(record, db_path=db)
    loaded = list_runs(db_path=db)[0]
    assert loaded.plan == "1. reconcile\n2. total"
    assert loaded.unverified == ["730.50"]
    assert loaded.corrections == 1


def test_receipts_default_to_empty(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    save_run(new_record("p", "m", "q", "a", []), db_path=db)
    loaded = list_runs(db_path=db)[0]
    assert loaded.plan == ""
    assert loaded.unverified == []
    assert loaded.corrections == 0


def test_list_runs_only_unverified(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    save_run(new_record("p", "m", "clean", "a", []), db_path=db)  # grounded
    save_run(
        new_record("p", "m", "healed", "a", [], corrections=1),  # self-corrected → clean
        db_path=db,
    )
    save_run(
        new_record("p", "m", "flagged", "a", [], unverified=["730.50"]),  # needs review
        db_path=db,
    )
    review = list_runs(db_path=db, only_unverified=True)
    assert [r.prompt for r in review] == ["flagged"]  # only the still-flagged run
    assert len(list_runs(db_path=db)) == 3  # unfiltered still sees all


def test_get_run_by_full_id_and_prefix(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    rec = RunRecord("abc123def456", "2026-01-01", "p", "m", "q", "a", [], plan="plan x")
    save_run(rec, db_path=db)
    assert get_run("abc123def456", db_path=db) is not None
    got = get_run("abc12", db_path=db)  # prefix
    assert got is not None and got.plan == "plan x"


def test_get_run_missing_and_ambiguous(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    save_run(RunRecord("aa11", "2026-01-01", "p", "m", "q", "a", []), db_path=db)
    save_run(RunRecord("aa22", "2026-01-02", "p", "m", "q", "a", []), db_path=db)
    assert get_run("zz", db_path=db) is None  # no match
    assert get_run("aa", db_path=db) is None  # ambiguous prefix → refuse to guess
    assert get_run("aa11", db_path=db) is not None  # exact still resolves


def test_get_run_no_db(tmp_path: Any) -> None:
    assert get_run("anything", db_path=tmp_path / "nope.db") is None


def test_migration_adds_columns_to_old_db(tmp_path: Any) -> None:
    # Simulate a pre-migration DB: a `runs` table without the new columns.
    import sqlite3

    db = tmp_path / "halia.db"
    old = sqlite3.connect(db)
    old.execute(
        "CREATE TABLE runs (id TEXT PRIMARY KEY, started_at TEXT, provider TEXT, "
        "model TEXT, prompt TEXT, answer TEXT, steps_json TEXT)"
    )
    old.execute(
        "INSERT INTO runs VALUES ('abc', '2026-01-01', 'p', 'm', 'q', 'a', '[]')"
    )
    old.commit()
    old.close()

    # connect() (via list_runs → save_run) should migrate in the missing columns.
    save_run(new_record("p", "m", "new", "a2", [], plan="do x"), db_path=db)
    loaded = {r.id: r for r in list_runs(db_path=db)}
    assert loaded["abc"].plan == ""  # backfilled default on the old row
    assert loaded["abc"].corrections == 0
    assert [r for r in list_runs(db_path=db) if r.plan == "do x"]  # new row kept its plan


def test_step_preview_truncates() -> None:
    step = Step("t", "{}", "x" * 500)
    preview = step.preview(50)
    assert preview.endswith("…")
    assert len(preview) <= 51
