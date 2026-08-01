"""Tests for the checkpointer — pause on approval, resume with a decision."""

from pathlib import Path
from typing import Any

from halia.config.settings import Config
from halia.core.agent import resume, run
from halia.core.checkpoint import (
    get_checkpoint,
    list_checkpoints,
    new_checkpoint,
    save_checkpoint,
)
from halia.providers.base import ChatResult, Message, ToolCall
from halia.skills import build_registry, default_registry

_CFG = Config(provider="x", model="m", base_url="u", api_key="k")


class FakeProvider:
    def __init__(self, results: list[ChatResult]) -> None:
        self._results = results
        self.calls = 0

    def chat(self, messages: list[Message], tools: Any = None) -> ChatResult:
        result = self._results[self.calls]
        self.calls += 1
        return result


def _write_call(path: Path) -> ChatResult:
    return ChatResult(
        content=None,
        tool_calls=[
            ToolCall(
                id="1",
                name="write_file",
                arguments=f'{{"path": "{path}", "content": "hi"}}',
            )
        ],
    )


def test_checkpoint_roundtrip(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    cp = new_checkpoint(
        prompt="do it", provider="p", model="m", skills=["write_file"], extra_system="",
        plan="1. write", messages=[{"role": "user", "content": "hi"}], steps=[],
        pending=[ToolCall(id="1", name="write_file", arguments="{}")],
        iters_used=1, corrections=0, reason="approval required: write_file",
    )
    save_checkpoint(cp, db_path=db)
    loaded = get_checkpoint(cp.id, db_path=db)
    assert loaded is not None
    assert loaded.skills == ["write_file"]
    assert loaded.pending[0]["name"] == "write_file"
    assert loaded.reason.startswith("approval required")
    assert len(list_checkpoints(db_path=db)) == 1


def test_run_pauses_at_dangerous_tool(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    target = tmp_path / "out.txt"
    provider = FakeProvider([_write_call(target), ChatResult(content="done", tool_calls=[])])
    result = run(
        "write the file", _CFG, default_registry(), provider=provider,
        pause_on_approval=True, checkpoint_db=db,
    )
    assert result.paused is True
    assert result.checkpoint_id
    assert provider.calls == 1  # stopped after the first turn — did not execute
    assert not target.exists()  # the dangerous tool did NOT run
    cp = get_checkpoint(result.checkpoint_id, db_path=db)
    assert cp is not None and cp.pending[0]["name"] == "write_file"


def test_resume_approve_executes_and_finishes(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    target = tmp_path / "out.txt"
    # pause
    paused = run(
        "write it", _CFG, default_registry(), provider=FakeProvider([_write_call(target)]),
        pause_on_approval=True, checkpoint_db=db,
    )
    cp = get_checkpoint(paused.checkpoint_id, db_path=db)
    assert cp is not None
    # resume + approve → the write happens, then the loop finishes
    result = resume(
        cp, _CFG, approve=True,
        provider=FakeProvider([ChatResult(content="wrote it", tool_calls=[])]),
        checkpoint_db=db,
    )
    assert result.paused is False
    assert result.answer == "wrote it"
    assert target.read_text() == "hi"  # approved → executed


def test_resume_deny_skips_the_action(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    target = tmp_path / "out.txt"
    paused = run(
        "write it", _CFG, default_registry(), provider=FakeProvider([_write_call(target)]),
        pause_on_approval=True, checkpoint_db=db,
    )
    cp = get_checkpoint(paused.checkpoint_id, db_path=db)
    assert cp is not None
    result = resume(
        cp, _CFG, approve=False,
        provider=FakeProvider([ChatResult(content="skipped it", tool_calls=[])]),
        checkpoint_db=db,
    )
    assert result.answer == "skipped it"
    assert not target.exists()  # denied → not executed
    # the denial is visible in the trace
    assert any("denied" in s.observation for s in result.steps)


def test_resume_rebuilds_registry_from_checkpoint_skills(tmp_path: Any) -> None:
    # No registry passed → resume rebuilds it from the checkpoint's skill list.
    db = tmp_path / "halia.db"
    target = tmp_path / "out.txt"
    paused = run(
        "write it", _CFG, build_registry(["write_file"]),
        provider=FakeProvider([_write_call(target)]),
        pause_on_approval=True, checkpoint_db=db,
    )
    cp = get_checkpoint(paused.checkpoint_id, db_path=db)
    assert cp is not None and "write_file" in cp.skills
    result = resume(
        cp, _CFG, approve=True,
        provider=FakeProvider([ChatResult(content="ok", tool_calls=[])]), checkpoint_db=db,
    )
    assert result.answer == "ok"
    assert target.read_text() == "hi"
