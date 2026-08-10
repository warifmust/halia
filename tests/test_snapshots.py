"""Tests for file-write snapshots (undo)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from halia.store import snapshots


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Any, monkeypatch: Any) -> None:
    # Redirect both the snapshot store and the DB to a temp dir — never touch ~/.halia.
    monkeypatch.setattr(snapshots, "SNAPSHOTS_DIR", tmp_path / "snaps")
    monkeypatch.setattr(snapshots, "DB_PATH", tmp_path / "halia.db")


def test_snapshot_returns_none_for_missing_file(tmp_path: Any) -> None:
    assert snapshots.snapshot_file(tmp_path / "nope.txt") is None


def test_restore_returns_none_when_empty() -> None:
    assert snapshots.restore_latest() is None


def test_snapshot_then_restore_roundtrip(tmp_path: Any) -> None:
    f = tmp_path / "doc.txt"
    f.write_text("original")
    snap = snapshots.snapshot_file(f)
    assert snap is not None and Path(snap).is_file()

    f.write_text("overwritten")  # simulate the overwrite
    restored = snapshots.restore_latest(str(f))
    assert restored is not None
    assert f.read_text() == "original"
    # pop semantics: the snapshot is consumed, so a second undo finds nothing
    assert snapshots.restore_latest(str(f)) is None


def test_undo_peels_back_multiple_versions(tmp_path: Any) -> None:
    f = tmp_path / "doc.txt"
    f.write_text("v1")
    snapshots.snapshot_file(f)  # backs up v1
    f.write_text("v2")
    snapshots.snapshot_file(f)  # backs up v2
    f.write_text("v3")

    assert snapshots.restore_latest(str(f))[0] == str(f.resolve())  # type: ignore[index]
    assert f.read_text() == "v2"  # newest snapshot first
    snapshots.restore_latest(str(f))
    assert f.read_text() == "v1"  # then the one before
    assert snapshots.restore_latest(str(f)) is None


def test_pathless_restore_targets_most_recent_write(tmp_path: Any) -> None:
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("a-old")
    b.write_text("b-old")
    snapshots.snapshot_file(a)
    snapshots.snapshot_file(b)  # most recent overall
    a.write_text("a-new")
    b.write_text("b-new")

    restored = snapshots.restore_latest()  # no path → most recent across all
    assert restored is not None and restored[0] == str(b.resolve())
    assert b.read_text() == "b-old" and a.read_text() == "a-new"


def test_write_file_snapshots_on_overwrite(tmp_path: Any) -> None:
    from halia.skills.fs import WriteFile

    f = tmp_path / "report.txt"
    # First write CREATES the file → no snapshot, no undo hint.
    r1 = WriteFile().run({"path": str(f), "content": "first"})
    assert "undo" not in r1
    assert snapshots.restore_latest(str(f)) is None

    # Second write OVERWRITES → snapshot taken, hint shown, and undo restores "first".
    r2 = WriteFile().run({"path": str(f), "content": "second"})
    assert "halia undo" in r2
    assert f.read_text() == "second"
    assert snapshots.restore_latest(str(f)) is not None
    assert f.read_text() == "first"


def test_retention_cap_bounds_growth(tmp_path: Any) -> None:
    f = tmp_path / "log.txt"
    for i in range(snapshots._KEEP_PER_PATH + 5):
        f.write_text(f"v{i}")
        snapshots.snapshot_file(f)
    kept = snapshots.list_snapshots(str(f), limit=100)
    assert len(kept) == snapshots._KEEP_PER_PATH  # older ones pruned
