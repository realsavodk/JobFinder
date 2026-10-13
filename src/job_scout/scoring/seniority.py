from __future__ import annotations

import re

# Title is the strongest signal when present (checked first, highest match wins).
_TITLE_MIN_YEARS = {
    "intern": 0,
    "internship": 0,
    "new grad": 0,
    "entry level": 0,
    "entry-level": 0,
    "junior": 0,
    "associate": 1,
    "senior": 5,
    "sr": 5,
    "staff": 7,
    "principal": 7,
    "lead": 5,
    "director": 8,
    "vice president": 8,
    "vp": 8,
    "head of": 8,
    "chief": 10,
}

_RANGE_RE = re.compile(r"(\d+)\s*(?:-|to)\s*\d+\+?\s*years?", re.IGNORECASE)
_PLUS_RE = re.compile(r"(\d+)\s*\+\s*years?", re.IGNORECASE)
_AT_LEAST_RE = re.compile(
    r"(?:at least|minimum(?: of)?)\s*(\d+)\s*years?", re.IGNORECASE
)
_PLAIN_RE = re.compile(r"(\d+)\s*years?\s*(?:of\s*)?experience", re.IGNORECASE)


def _title_min_years(title: str) -> int | None:
    title_lower = title.lower()
    matches = [
        years
        for keyword, years in _TITLE_MIN_YEARS.items()
        if re.search(rf"\b{re.escape(keyword)}\b", title_lower)
    ]
    return max(matches) if matches else None


def _text_min_years(text: str) -> int | None:
    # Conservative on purpose: take the highest number found anywhere rather than
    # the first, so a role that mentions "5+ years" isn't waved through because a
    # softer "2+ years in SQL" appears elsewhere in the description.
    candidates = [int(match.group(1)) for match in _RANGE_RE.finditer(text)]
    # Range upper bounds (the "5" in "3-5 years") aren't independent requirements —
    # strip them out before scanning for standalone "N years" mentions, otherwise
    # a 3-5 year range gets conflated with a flat 5-year requirement.
    remaining_text = _RANGE_RE.sub(" ", text)
    candidates += [
        int(match.group(1))
        for pattern in (_PLUS_RE, _AT_LEAST_RE, _PLAIN_RE)
        for match in pattern.finditer(remaining_text)
    ]
    return max(candidates) if candidates else None


def estimate_min_years(title: str, text: str) -> int | None:
    return (
        _title_min_years(title)
        if _title_min_years(title) is not None
        else _text_min_years(text)
    )
