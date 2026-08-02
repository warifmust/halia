"""Text metrics — count characters/words and check copy against a channel limit.

The deterministic trust hook for the marketing vertical: "good copy" is subjective,
but "this headline is 47 characters, over the 40-char limit" is a hard pass/fail. The
counts are tool figures, so the number-grounding conscience verifies any length the
answer claims.

Well-known, relatively stable hard limits are built in; for anything authoritative pass
an explicit `limit`, since platform rules change (and platforms may count URLs/emoji
specially — this counts raw characters).
"""

from __future__ import annotations

import re
from typing import Any

_WORD = re.compile(r"\S+")

# Conservative, well-known limits. Prefer an explicit `limit` for authoritative checks.
_PLATFORM_LIMITS: dict[str, int] = {
    "twitter": 280,
    "x": 280,
    "sms": 160,
    "google_ads_headline": 30,
    "google_ads_description": 90,
    "meta_ad_headline": 40,
    "instagram_caption": 2200,
    "linkedin_post": 3000,
    "email_subject": 60,  # recommended, not hard
    "meta_description_seo": 160,  # recommended
}


class CountText:
    name = "count_text"
    description = (
        "Count characters and words in text, and optionally check it fits a length limit. "
        "Pass `limit` for an exact character budget, or `platform` (e.g. twitter, sms, "
        "google_ads_headline, meta_ad_headline, email_subject). Use to VERIFY copy fits a "
        "channel instead of guessing."
    )
    dangerous = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "text": {"type": "string", "description": "The text to measure."},
            "limit": {"type": "integer", "description": "A character limit to check against."},
            "platform": {"type": "string", "description": "A known channel to use its limit."},
        },
        "required": ["text"],
    }

    def run(self, args: dict[str, Any]) -> str:
        text = args.get("text")
        if not isinstance(text, str) or text == "":
            return "error: 'text' is required and must be a non-empty string"

        chars = len(text)
        no_spaces = len(text.replace(" ", "").replace("\n", "").replace("\t", ""))
        words = len(_WORD.findall(text))
        base = f"{chars} characters ({no_spaces} without spaces), {words} words."

        limit = args.get("limit")
        platform = args.get("platform")
        where = ""  # e.g. " (twitter)"
        if not isinstance(limit, int) and isinstance(platform, str):
            key = platform.strip().lower()
            if key not in _PLATFORM_LIMITS:
                known = ", ".join(sorted(_PLATFORM_LIMITS))
                return f"{base} Unknown platform '{platform}'. Known: {known}. Or pass `limit`."
            limit = _PLATFORM_LIMITS[key]
            where = f" ({key})"

        if isinstance(limit, int) and limit > 0:
            if chars <= limit:
                return f"{base} Within the {limit}-char limit{where} — {limit - chars} to spare."
            return f"{base} OVER the {limit}-char limit{where} by {chars - limit}."
        return base
