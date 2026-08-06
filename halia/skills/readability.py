"""Readability skill — measure the reading level of text.

A deterministic trust hook for the education vertical: instead of *claiming* "this
reads at a 3rd-grade level", the agent measures it. Uses the Flesch–Kincaid grade
formula (standard, dependency-free — just word/sentence/syllable counts). The grade
it returns is a tool figure, so the number-grounding conscience verifies any level
the answer asserts.
"""

from __future__ import annotations

import re
from typing import Any

_WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")
_SENTENCE_END = re.compile(r"[.!?]+")
_VOWELS = "aeiouy"


def _syllables(word: str) -> int:
    """Heuristic syllable count: vowel groups, minus a silent trailing 'e'. Min 1."""
    word = word.lower()
    count = 0
    prev_vowel = False
    for ch in word:
        is_vowel = ch in _VOWELS
        if is_vowel and not prev_vowel:
            count += 1
        prev_vowel = is_vowel
    if word.endswith("e") and count > 1:
        count -= 1
    return max(1, count)


def flesch_kincaid_grade(text: str) -> tuple[float, int, int, int]:
    """Return (grade, words, sentences, syllables) for `text`."""
    words = _WORD.findall(text)
    n_words = len(words)
    n_sentences = max(1, len(_SENTENCE_END.findall(text)))
    n_syllables = sum(_syllables(w) for w in words)
    if n_words == 0:
        return (0.0, 0, n_sentences, 0)
    grade = 0.39 * (n_words / n_sentences) + 11.8 * (n_syllables / n_words) - 15.59
    return (round(grade, 1), n_words, n_sentences, n_syllables)


class Readability:
    name = "readability"
    description = (
        "Measure the reading level of text: Flesch-Kincaid grade level plus word, "
        "sentence, and syllable counts. Use this to CHECK that material matches a target "
        "grade instead of guessing the level."
    )
    dangerous = False
    untrusted = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "text": {"type": "string", "description": "The text to measure."},
        },
        "required": ["text"],
    }

    def run(self, args: dict[str, Any]) -> str:
        text = args.get("text")
        if not isinstance(text, str) or not text.strip():
            return "error: 'text' is required and must be a non-empty string"
        grade, words, sentences, syllables = flesch_kincaid_grade(text)
        if words == 0:
            return "no words to measure"
        return (
            f"Flesch-Kincaid grade level: {grade} "
            f"(words: {words}, sentences: {sentences}, syllables: {syllables})"
        )
