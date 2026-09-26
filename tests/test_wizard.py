"""Tests for the `halia setup` wizard helpers."""

from halia.config.wizard import _model_options


def test_model_options_appends_custom_choice() -> None:
    assert _model_options(["a", "b"]) == ["a", "b", "Custom model…"]


def test_model_options_no_duplicate_custom_choice() -> None:
    assert _model_options(["a", "Custom model…"]) == ["a", "Custom model…"]
