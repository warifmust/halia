"""Tests for count_text (character/word counts + channel-limit check)."""

from halia.skills.textmetrics import CountText


def test_basic_counts() -> None:
    out = CountText().run({"text": "Hello world"})
    assert "11 characters" in out
    assert "2 words" in out


def test_within_platform_limit() -> None:
    out = CountText().run({"text": "Short and sweet", "platform": "twitter"})
    assert "Within the 280-char limit (twitter)" in out
    assert "265 to spare" in out


def test_over_platform_limit() -> None:
    out = CountText().run({"text": "x" * 47, "platform": "meta_ad_headline"})
    assert "OVER the 40-char limit (meta_ad_headline) by 7" in out


def test_explicit_limit_takes_precedence() -> None:
    out = CountText().run({"text": "x" * 35, "limit": 30})
    assert "OVER the 30-char limit by 5" in out


def test_unknown_platform_lists_known() -> None:
    out = CountText().run({"text": "hi", "platform": "myspace"})
    assert "Unknown platform" in out and "twitter" in out


def test_requires_text() -> None:
    assert "required" in CountText().run({"text": ""})


def test_is_safe_and_wired() -> None:
    from halia.skills import available_skills

    assert CountText().dangerous is False
    assert "count_text" in available_skills()
