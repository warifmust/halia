"""Tests for history compaction — summarise older turns instead of hard-dropping them."""

from dataclasses import replace
from typing import Any

from halia.config.settings import Config
from halia.core.agent import compact_history, converse
from halia.core.session import get_session, new_session, save_session
from halia.providers.base import ChatResult, Message
from halia.skills import default_registry

_CFG = Config(provider="x", model="m", base_url="u", api_key="k")


class FakeProvider:
    def __init__(self, results: list[ChatResult]) -> None:
        self._results = results
        self.calls = 0
        self.seen: list[list[Message]] = []

    def chat(self, messages: list[Message], tools: Any = None) -> ChatResult:
        self.seen.append(list(messages))
        result = self._results[self.calls]
        self.calls += 1
        return result


def _big_history() -> list[Message]:
    """A system prompt + several long turns, ending on a user message."""
    msgs: list[Message] = [{"role": "system", "content": "sys"}]
    for i in range(6):
        msgs.append({"role": "user", "content": f"question {i} " + "x" * 200})
        msgs.append({"role": "assistant", "content": f"answer {i} " + "y" * 200})
    msgs.append({"role": "user", "content": "final question " + "z" * 100})
    return msgs


def test_compact_history_replaces_old_turns_with_summary() -> None:
    provider = FakeProvider([ChatResult("SUMMARY: earlier work happened", [])])
    messages = _big_history()
    original_len = len(messages)

    dropped = compact_history(messages, _CFG, provider=provider, keep_recent_chars=400)

    assert dropped  # something was summarised away
    assert messages[0]["content"] == "sys"  # system prompt kept verbatim
    assert "Summary of earlier conversation" in str(messages[1]["content"])
    assert "SUMMARY: earlier work happened" in str(messages[1]["content"])
    assert len(messages) < original_len  # working set shrank
    # the kept tail begins at a user boundary (no dangling tool responses)
    assert messages[2]["role"] == "user"


def test_compact_history_noop_when_small() -> None:
    provider = FakeProvider([ChatResult("unused", [])])
    messages: list[Message] = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]
    dropped = compact_history(messages, _CFG, provider=provider, keep_recent_chars=400)
    assert dropped == []  # nothing old enough to summarise
    assert provider.calls == 0  # never even asked the model to summarise


def test_converse_compacts_when_over_threshold_and_consented() -> None:
    # First model call inside the loop is the SUMMARY; second is the actual answer.
    provider = FakeProvider(
        [ChatResult("dense summary", []), ChatResult("here is your answer", [])]
    )
    messages = _big_history()
    dropped_seen: list[Message] = []

    result = converse(
        messages, _CFG, default_registry(), provider=provider,
        history_budget=1000,  # small budget so the big history is well over 85%
        compact_approver=lambda: True,
        on_compact=lambda dropped: dropped_seen.extend(dropped),
    )

    assert result.answer == "here is your answer"
    assert dropped_seen  # on_compact fired with the summarised-away turns
    # the answer call saw the compacted window (summary note present, old bulk gone)
    answer_window = provider.seen[1]
    assert any("Summary of earlier conversation" in str(m.get("content")) for m in answer_window)


def test_converse_skips_compaction_when_declined() -> None:
    provider = FakeProvider([ChatResult("answer without compaction", [])])
    messages = _big_history()

    result = converse(
        messages, _CFG, default_registry(), provider=provider,
        history_budget=1000,
        compact_approver=lambda: False,  # user said no
        on_compact=lambda dropped: (_ for _ in ()).throw(AssertionError("should not compact")),
    )

    assert result.answer == "answer without compaction"
    assert provider.calls == 1  # only the answer call — no summarisation happened


def test_session_archives_compacted_transcript(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    s = new_session("p", "m", None, False, [{"role": "system", "content": "sys"}])
    archived: list[Message] = [{"role": "user", "content": "old turn"}]
    save_session(replace(s, archived_messages=archived), db_path=db)
    loaded = get_session(s.id, db_path=db)
    assert loaded is not None
    assert loaded.archived_messages == archived  # full transcript preserved across restart
