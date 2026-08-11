"""Tests for failure memory (Tier 2)."""

from __future__ import annotations

from typing import Any

from halia.memory.failures import (
    _MAX_FAILURES,
    failures_advisory,
    forget_failure,
    list_failures,
    recall_failures,
    record_failure,
)


def test_record_and_list(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    f = record_failure("test the login flow end to end", "hit iteration cap (50)", "qa", db_path=db)
    assert f is not None
    items = list_failures(db_path=db)
    assert len(items) == 1 and items[0].cause.startswith("hit iteration cap")
    assert items[0].profile == "qa"


def test_record_ignores_empty_and_trims_cause(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    assert record_failure("", "cause", db_path=db) is None
    assert record_failure("prompt", "  ", db_path=db) is None
    long = record_failure("p", "x" * 500, db_path=db)
    assert long is not None and len(long.cause) <= 200


def test_recall_matches_similar_task(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    record_failure("test the change mobile number webhook", "iteration cap", db_path=db)
    record_failure("summarise the quarterly finance report", "provider timeout", db_path=db)

    hits = recall_failures("run the change mobile number tests", db_path=db)
    assert hits and "mobile number" in hits[0].prompt.lower()
    assert not any("finance" in h.prompt.lower() for h in hits)


def test_advisory_labels_as_hint_not_fact(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    record_failure("deploy the staging service", "connection refused", db_path=db)
    adv = failures_advisory("deploy the staging service again", db_path=db)
    assert "ADVISORY" in adv and "connection refused" in adv
    assert "not established facts" in adv.lower() or "not an established fact" in adv.lower()
    # no match → empty (no advisory injected)
    assert failures_advisory("something totally unrelated zzz", db_path=db) == ""


def test_forget(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    f = record_failure("a task", "some cause", db_path=db)
    assert f is not None
    assert forget_failure(f.id[:4], db_path=db) is True
    assert list_failures(db_path=db) == []


def test_store_is_bounded(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    for i in range(_MAX_FAILURES + 10):
        record_failure(f"task number {i}", f"cause {i}", db_path=db)
    assert len(list_failures(db_path=db)) == _MAX_FAILURES


def test_soft_signals_are_not_failures() -> None:
    # Documents the design call: only HARD/objective failures are recorded. A conscience
    # correction is the trust floor working, not a failure — nothing in this module records it.
    import halia.memory.failures as fm

    src = fm.__doc__ or ""
    assert "objective" in src.lower()
    assert "no self-assessment" in src.lower()
