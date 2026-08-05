"""Tests for the number-grounding verifier."""

from halia.audit.trace import Step
from halia.conscience.verify import ungrounded_numbers


def test_flags_invented_figures() -> None:
    steps = [Step("reconcile_csv", "{}", "T2: 250.00 vs 255.00 matched keys: 3")]
    answer = "Bank total is 730.50, net difference 175.00. T2 was 250.00 vs 255.00."
    out = ungrounded_numbers(answer, steps)
    assert "730.50" in out  # invented — not in any tool output
    assert "175.00" in out  # invented
    assert "250.00" not in out  # grounded (appears in the observation)
    assert "255.00" not in out


def test_grounded_figure_not_flagged() -> None:
    # tool output "4" (bare int) grounds the answer's "4.00" (equal as Decimal)
    steps = [Step("calculate", '{"expression": "2 + 2"}', "4")]
    assert ungrounded_numbers("The total is 4.00.", steps) == []


def test_ignores_dates_and_counts() -> None:
    steps = [Step("read_csv", "{}", "date | amount 2026-07-01 | 100.00")]
    answer = "On 2026-07-01 there were 3 rows totaling 100.00."
    # date + bare count are not checked; 100.00 is grounded
    assert ungrounded_numbers(answer, steps) == []


def test_no_figures_is_empty() -> None:
    steps = [Step("list_files", "{}", "a.txt b.txt")]
    assert ungrounded_numbers("There are some files here.", steps) == []


def test_correct_rounding_is_grounded() -> None:
    steps = [Step("aggregate_csv", "{}", "mean(amount) = 181.375 over 4 values")]
    # 181.38 is the correct 2-dp rounding of the tool's 181.375 → grounded
    assert ungrounded_numbers("The average is 181.38.", steps) == []


def test_mis_rounding_is_flagged() -> None:
    steps = [Step("aggregate_csv", "{}", "mean(amount) = 181.375 over 4 values")]
    # 181.40 is NOT a correct rounding of 181.375 → flagged
    assert "181.40" in ungrounded_numbers("The average is 181.40.", steps)


def test_negative_tool_figure_grounds_positive_magnitude() -> None:
    # Bank debits are negative in tools; prose states the positive magnitude — same fact.
    steps = [Step("reconcile_csv", "{}", "CHK-1042: -1200.00 vs -1250.00")]
    answer = "The check cleared for $1,250.00 versus the recorded $1,200.00."
    assert ungrounded_numbers(answer, steps) == []  # sign-insensitive → grounded


def test_positive_tool_figure_grounds_negative_answer() -> None:
    steps = [Step("aggregate_csv", "{}", "sum = 175.00")]
    assert ungrounded_numbers("The net difference is -175.00.", steps) == []


def test_sign_insensitivity_still_catches_invented() -> None:
    # magnitude matching must not ground a number that isn't in tools at all
    steps = [Step("reconcile_csv", "{}", "CHK-1042: -1200.00 vs -1250.00")]
    assert "999.00" in ungrounded_numbers("There is also a $999.00 fee.", steps)


def test_version_after_capitalized_word_is_not_a_figure() -> None:
    # "Sonnet 4.5" / "GPT-4.5" / "Claude 3.5" are names, not computed figures — no tools ran,
    # yet the decimal must NOT be flagged (the false positive we saw in a chat turn).
    assert ungrounded_numbers("I'm Sonnet 4.5, your assistant.", []) == []
    assert ungrounded_numbers("Running GPT-4.5 under the hood.", []) == []
    assert ungrounded_numbers("That's Claude 3.5 Sonnet, not Version 4.5.", []) == []
    assert ungrounded_numbers("See Table 4.5 for details.", []) == []


def test_real_figure_after_lowercase_or_label_still_caught() -> None:
    # The heuristic must not swallow genuine figures: a lowercase connector, a colon label,
    # or a currency mark before the number still gets ground-checked.
    assert ungrounded_numbers("The total is 4.5 million.", []) == ["4.5"]
    assert ungrounded_numbers("Revenue: 4.5 recorded.", []) == ["4.5"]
    assert "4.5" in ungrounded_numbers("It costs $4.5 today.", [])
    assert ungrounded_numbers("revenue was 4.5 overall.", []) == ["4.5"]


def test_money_after_capitalized_word_still_caught() -> None:
    # Version-like X.Y is exempt after a capital, but a 2-decimal / comma money figure is
    # NOT — even when a capitalized (often sentence-initial) word precedes it.
    assert ungrounded_numbers("Still 730.50 remains.", []) == ["730.50"]
    assert ungrounded_numbers("Total 42.00 outstanding.", []) == ["42.00"]
    assert "1250.00" in ungrounded_numbers("Balance 1,250.00 today.", [])
