"""Resolve a Seedance duration from explicit input or natural-language briefs."""

from __future__ import annotations

import re


MIN_DURATION_SECONDS = 4
MAX_DURATION_SECONDS = 15
DEFAULT_DURATION_SECONDS = 10

_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}
_NUMBER_TOKEN = r"(?:\d{1,2}|" + "|".join(_NUMBER_WORDS) + r")"
_SECONDS = r"(?:seconds?|secs?|s)\b"


class DurationRequestError(ValueError):
    """The requested duration cannot be represented by Seedance 2.0."""


def _to_int(value: str) -> int:
    return int(value) if value.isdigit() else _NUMBER_WORDS[value]


def _validate(seconds: int, *, requested: str) -> int:
    if seconds < MIN_DURATION_SECONDS or seconds > MAX_DURATION_SECONDS:
        raise DurationRequestError(
            f"Seedance 2.0 supports {MIN_DURATION_SECONDS}–"
            f"{MAX_DURATION_SECONDS} second clips; {requested} resolves to "
            f"{seconds} seconds."
        )
    return seconds


def duration_from_brief(brief: str) -> int | None:
    """Extract the user's duration instruction, preserving intent.

    Examples:
    - "less than 5 seconds" -> 4
    - "under ten seconds" -> 9
    - "a 7-second ad" -> 7
    - no duration language -> None
    """
    text = brief.lower().replace("–", "-").replace("—", "-")

    strict_less = re.search(
        rf"(?:less|fewer|shorter)\s+than\s+({_NUMBER_TOKEN})\s*{_SECONDS}"
        rf"|under\s+({_NUMBER_TOKEN})\s*{_SECONDS}"
        rf"|<\s*({_NUMBER_TOKEN})\s*{_SECONDS}",
        text,
    )
    if strict_less:
        raw = next(group for group in strict_less.groups() if group)
        seconds = _to_int(raw) - 1
        return _validate(seconds, requested=strict_less.group(0))

    at_most = re.search(
        rf"(?:at\s+most|no\s+more\s+than|up\s+to|max(?:imum)?)\s+"
        rf"({_NUMBER_TOKEN})\s*{_SECONDS}",
        text,
    )
    if at_most:
        return _validate(_to_int(at_most.group(1)), requested=at_most.group(0))

    strict_more = re.search(
        rf"(?:more|longer)\s+than\s+({_NUMBER_TOKEN})\s*{_SECONDS}"
        rf"|over\s+({_NUMBER_TOKEN})\s*{_SECONDS}"
        rf"|>\s*({_NUMBER_TOKEN})\s*{_SECONDS}",
        text,
    )
    if strict_more:
        raw = next(group for group in strict_more.groups() if group)
        seconds = _to_int(raw) + 1
        return _validate(seconds, requested=strict_more.group(0))

    exact = re.search(
        rf"\b({_NUMBER_TOKEN})\s*(?:-\s*)?{_SECONDS}", text
    )
    if exact:
        return _validate(_to_int(exact.group(1)), requested=exact.group(0))

    return None


def resolve_duration(brief: str, explicit: int | None, default: int) -> int:
    """Explicit API/UI input wins; otherwise use the brief, then the default."""
    if explicit is not None:
        return _validate(explicit, requested=f"explicit duration {explicit}")
    from_brief = duration_from_brief(brief)
    if from_brief is not None:
        return from_brief
    return _validate(default, requested=f"default duration {default}")
