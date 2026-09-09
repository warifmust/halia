"""Tests for the deterministic parts of the computer-use eval harness."""

from halia.audit.trace import Step
from halia.eval.harness import (
    AutoApprove,
    classify_guard,
    classify_observation,
    guard_breakdown,
    harness_note,
    score_steps,
)
from halia.eval.tasks import _count_claims, _fabrications


def test_classify_observation() -> None:
    assert classify_observation("error: boom") == "error"
    assert classify_observation("error opening https://x") == "error"
    assert classify_observation("circuit breaker: 'x' has failed 3 times") == "guard"
    assert classify_observation("repetition guard: 'x' tried 2 times") == "guard"
    assert classify_observation("exploration guard: 16 consecutive screenshots") == "guard"
    assert classify_observation("screenshot budget exceeded: 24 screenshots this run") == "guard"
    assert classify_observation("Clicked element: #login") == "ok"


def test_score_steps_counts_errors_and_guards() -> None:
    steps = [
        Step("browser_click", "{}", "Clicked element"),
        Step("browser_click", "{}", "error clicking: timeout"),
        Step("browser_click", "{}", "circuit breaker: skipping"),
        Step("browser_click", "{}", "repetition guard: skipping"),
    ]
    actions, errors, guards = score_steps(steps)
    assert actions == 4
    assert errors == 1
    assert guards == 2


def test_classify_guard_kinds() -> None:
    assert classify_guard("repetition guard: 'x' tried 2 times") == "repetition"
    assert classify_guard("circuit breaker: 'x' has failed 3 times") == "circuit_breaker"
    assert classify_guard("exploration guard: 16 consecutive screenshots") == "exploration"
    assert classify_guard("screenshot budget exceeded: 24 screenshots") == "screenshot_budget"
    assert classify_guard("Clicked element: #login") is None


def test_guard_breakdown_counts_kinds() -> None:
    steps = [
        Step("a", "{}", "repetition guard: x tried 2 times"),
        Step("b", "{}", "repetition guard: y tried 2 times"),
        Step("c", "{}", "circuit breaker: z failed 3 times"),
        Step("d", "{}", "ok"),
    ]
    assert guard_breakdown(steps) == {"repetition": 2, "circuit_breaker": 1}


def test_harness_note_flags_high_guard_rate() -> None:
    assert harness_note(0, 0) == ""  # no actions → nothing to flag
    assert harness_note(10, 1) == ""  # 10% guards → healthy
    assert "50%" in harness_note(10, 5)  # 50% guards → flagged for investigation


def test_autodraw_rocket_verify_requires_screenshot_and_drag() -> None:
    from halia.core.agent import RunResult
    from halia.eval.tasks import autodraw_rocket_verify

    screenshot = Step("cua_screenshot", "{}", "Screenshot captured (1600x1039).")
    drag = Step(
        "cua_drag", '{"from_x": 1, "from_y": 2, "to_x": 3, "to_y": 4}', "Dragged"
    )

    assert autodraw_rocket_verify(
        RunResult(answer="", steps=[screenshot, drag])
    ).passed is True

    missing_drag = autodraw_rocket_verify(RunResult(answer="", steps=[screenshot]))
    assert missing_drag.passed is False
    assert "no cua_drag" in missing_drag.details


def test_auto_approve_grants_every_gate() -> None:
    approver = AutoApprove()
    assert approver("run_command", "{}") is True
    assert approver.check_consent("browser_open") is True
    assert approver.check_read("read_file", "{}") is True


def test_count_claims_extracts_number_noun_pairs() -> None:
    assert (6, "products") in _count_claims("We added 6 products, 1 each.")


def test_fabrications_flags_count_claims_mismatching_reality() -> None:
    assert _fabrications("There are 5 items in the cart.", 6) == (
        "answer claimed '5 items' but the cart has 6",
    )
    assert _fabrications("All 6 products are in the cart.", 6) == ()


def test_parse_models_splits_commas() -> None:
    from halia.eval.__main__ import _parse_models

    assert _parse_models(["a", "b,c"]) == ["a", "b", "c"]
