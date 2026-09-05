"""Tests for the deterministic parts of the computer-use eval harness."""

from halia.audit.trace import Step
from halia.eval.harness import AutoApprove, classify_observation, score_steps
from halia.eval.tasks import _count_claims, _fabrications


def test_classify_observation() -> None:
    assert classify_observation("error: boom") == "error"
    assert classify_observation("error opening https://x") == "error"
    assert classify_observation("circuit breaker: 'x' has failed 3 times") == "guard"
    assert classify_observation("repetition guard: 'x' tried 2 times") == "guard"
    assert classify_observation("exploration guard: 16 consecutive screenshots") == "guard"
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
