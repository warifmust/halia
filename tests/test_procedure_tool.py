"""Tests for the save_procedure skill (model-callable teach) and the NL approver."""

from typing import Any

import halia.procedures as P
from halia.skills.procedure_tool import SaveProcedure


def _skill_on(db: Any) -> Any:
    """Return a run() bound to a temp DB by patching the store's default path."""
    orig_save, orig_get = P.save_procedure, P.get_procedure
    P.save_procedure = lambda pr, db_path=db: orig_save(pr, db_path=db_path)
    P.get_procedure = lambda n, db_path=db: orig_get(n, db_path=db_path)
    return SaveProcedure().run


def test_save_full_procedure_reports_ready(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    run = _skill_on(db)
    out = run({
        "name": "login-api", "target": "POST /auth/login", "data_spec": "3 rows",
        "url": "https://x/login", "method": "POST",
        "result_columns": ["id", "verdict"], "pass_rule": "status==200",
    })
    assert "saved procedure 'login-api'" in out
    assert "ready to run" in out
    loaded = P.get_procedure("login-api", db_path=db)
    assert loaded is not None and loaded.method == "POST"


def test_save_incomplete_reports_missing(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    run = _skill_on(db)
    out = run({"name": "half", "target": "something"})
    assert "still missing" in out
    assert "pass_rule" in out


def test_merge_semantics_preserve_omitted_fields(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    run = _skill_on(db)
    run({"name": "p", "target": "POST /x", "url": "https://x", "pass_rule": "ok"})
    # a later call that only sets result_columns must NOT wipe url/pass_rule
    run({"name": "p", "result_columns": ["a", "b"]})
    loaded = P.get_procedure("p", db_path=db)
    assert loaded is not None
    assert loaded.url == "https://x"
    assert loaded.pass_rule == "ok"
    assert loaded.result_columns == ["a", "b"]


def test_save_multi_step_no_url(tmp_path: Any) -> None:
    db = tmp_path / "halia.db"
    run = _skill_on(db)
    out = run({
        "name": "flow", "target": "onboarding", "data_spec": "1 user",
        "steps": ["create account", "check welcome email"],
        "result_columns": ["step", "verdict"], "pass_rule": "each step ok",
    })
    assert "ready to run" in out  # steps satisfy the action requirement
    loaded = P.get_procedure("flow", db_path=db)
    assert loaded is not None and loaded.steps == ["create account", "check welcome email"]


def test_requires_name() -> None:
    assert "name" in SaveProcedure().run({})


def test_gated_and_wired() -> None:
    from halia.presets import get_preset
    from halia.skills import DEFAULT_SKILLS, available_skills, default_registry

    assert SaveProcedure().dangerous is True  # persistence → approval-gated (the confirm)
    assert "save_procedure" in available_skills()
    assert "save_procedure" in DEFAULT_SKILLS  # halia can teach from any chat
    assert default_registry().get("save_procedure") is not None
    qa = get_preset("qa")
    assert qa is not None and "save_procedure" in qa.skills


# --- conversational approver ---


def test_is_affirmative_accepts_natural_yes() -> None:
    from halia.cli.main import _is_affirmative

    for yes in ["y", "yes", "yeah", "yep", "sure", "ok", "go ahead", "lets do it", "please do"]:
        assert _is_affirmative(yes) is True, yes


def test_is_affirmative_rejects_no_and_stops() -> None:
    from halia.cli.main import _is_affirmative

    for no in ["no", "nope", "wait", "stop", "not yet", "hold on", "cancel", ""]:
        assert _is_affirmative(no) is False, no


def test_is_affirmative_first_word_wins() -> None:
    from halia.cli.main import _is_affirmative

    # a stray "no" later in an affirmative reply must NOT flip it (the real-world bug)
    assert _is_affirmative("yes, and no bearer token needed") is True
    assert _is_affirmative("sure, go ahead") is True
    # a leading no/stop still wins
    assert _is_affirmative("no, wait — the url is wrong") is False
