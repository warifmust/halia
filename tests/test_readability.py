"""Tests for the readability skill (Flesch-Kincaid grade)."""

from halia.skills.readability import Readability, flesch_kincaid_grade


def test_counts_on_a_known_sentence() -> None:
    grade, words, sentences, syllables = flesch_kincaid_grade("The cat sat on the mat.")
    assert words == 6
    assert sentences == 1
    assert syllables == 6  # each word is one syllable


def test_simple_text_is_low_grade() -> None:
    grade, *_ = flesch_kincaid_grade("The cat sat on the mat. The dog ran.")
    assert grade < 3.0  # very easy reading


def test_complex_text_is_higher_grade() -> None:
    simple, *_ = flesch_kincaid_grade("The cat sat. The dog ran. We had fun.")
    complex_, *_ = flesch_kincaid_grade(
        "The utilization of sophisticated methodologies necessitates comprehensive "
        "evaluation of interdependent organizational considerations."
    )
    assert complex_ > simple
    assert complex_ > 10.0  # dense, multisyllabic → college-ish


def test_deterministic() -> None:
    text = "Photosynthesis converts sunlight into energy."
    assert flesch_kincaid_grade(text) == flesch_kincaid_grade(text)


def test_skill_reports_grade_and_counts() -> None:
    out = Readability().run({"text": "The cat sat on the mat."})
    assert "Flesch-Kincaid grade level" in out
    assert "words: 6" in out


def test_skill_requires_text() -> None:
    assert "required" in Readability().run({"text": "   "})


def test_skill_is_safe() -> None:
    assert Readability().dangerous is False


def test_wired_into_catalogue_and_education_preset() -> None:
    from halia.presets import get_preset
    from halia.skills import available_skills, build_registry

    assert "readability" in available_skills()
    assert build_registry(["readability"]).get("readability") is not None
    edu = get_preset("education")
    assert edu is not None and "readability" in edu.skills
