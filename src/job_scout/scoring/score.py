from __future__ import annotations

import re
from dataclasses import dataclass

from job_scout.llm.text import strip_html
from job_scout.models import Posting
from job_scout.regions import regions_in
from job_scout.scoring.profile import Profile
from job_scout.scoring.seniority import estimate_min_years

TOOL_WEIGHT = 1.0
MAJOR_WEIGHT = 2.0
# LLM-extracted postings: the share of the job's required skills you have counts
# for this much of the score, its nice-to-haves for the rest.
REQUIRED_SHARE = 0.8
# A list this short is usually extraction missing skills, so one match out of one
# shouldn't read as a perfect fit: shorter lists count as this long.
MIN_LISTED_SKILLS = 3


@dataclass
class ScoreResult:
    score: int
    reasons: list[str]


def _posting_text(posting: Posting) -> str:
    description = strip_html(posting.description_html)
    return f"{posting.title} {posting.location} {description}".lower()


def _mentions(text: str, term: str) -> bool:
    # Whole-word match: plain substring matching made "C" hit every posting,
    # "Java" hit "JavaScript" and "SHAP" hit "shape".
    return re.search(rf"(?<!\w){re.escape(term.lower())}(?![\w+#])", text) is not None


def _same_region(location: str, profile: Profile) -> bool:
    # Province-level on purpose: "Ontario" or "Toronto" in the profile matches any
    # Ontario posting; users can skip ones that are too far.
    wanted = set().union(*(regions_in(loc) for loc in profile.locations))
    return bool(wanted & regions_in(location))


def _remote_country_ok(location: str, profile: Profile) -> bool:
    country = profile.remote_country
    return (
        country is None
        or country.lower() in location.lower()
        or _same_region(location, profile)
    )


def location_matches(location: str, profile: Profile) -> bool:
    lower = location.lower()
    return (
        any(loc.lower() in lower for loc in profile.locations)
        or _same_region(location, profile)
        or (
            profile.remote_ok
            and "remote" in lower
            and _remote_country_ok(location, profile)
        )
    )


def title_matches(title: str, profile: Profile) -> bool:
    return not profile.title_keywords or any(
        _mentions(title.lower(), keyword) for keyword in profile.title_keywords
    )


def is_relevant(title: str, location: str, profile: Profile) -> bool:
    """Worth fetching and storing at all: the right kind of role, somewhere wanted."""
    return title_matches(title, profile) and (
        not profile.locations or location_matches(location, profile)
    )


def _location_matches(posting: Posting, profile: Profile) -> bool:
    return location_matches(posting.location, profile)


def score_posting(posting: Posting, profile: Profile) -> ScoreResult:
    if posting.extraction_status == "success":
        return _structured_score(posting, profile)
    return keyword_score(posting, profile)


def keyword_score(posting: Posting, profile: Profile) -> ScoreResult:
    text = _posting_text(posting)

    for keyword in profile.exclude_keywords:
        if keyword.lower() in text:
            return ScoreResult(score=0, reasons=[f"excluded: contains {keyword!r}"])

    if profile.locations and not _location_matches(posting, profile):
        return ScoreResult(score=0, reasons=["excluded: location/remote mismatch"])

    min_years = estimate_min_years(posting.title, text)
    if min_years is not None and min_years > profile.years_experience + 1:
        reason = (
            f"excluded: looks like it needs ~{min_years}+ years "
            f"(you have {profile.years_experience})"
        )
        return ScoreResult(score=0, reasons=[reason])

    matched_weight = 0.0
    max_weight = 0.0
    reasons: list[str] = []

    for skill in profile.skills:
        max_weight += skill.weight
        if _mentions(text, skill.name):
            matched_weight += skill.weight
            reasons.append(skill.name)

    for tool in profile.tools:
        max_weight += TOOL_WEIGHT
        if _mentions(text, tool):
            matched_weight += TOOL_WEIGHT
            reasons.append(tool)

    for major in profile.majors:
        max_weight += MAJOR_WEIGHT
        if _mentions(text, major):
            matched_weight += MAJOR_WEIGHT
            reasons.append(major)

    score = round(100 * matched_weight / max_weight) if max_weight else 0
    return ScoreResult(score=score, reasons=reasons or ["no profile keywords matched"])


def _structured_location_matches(posting: Posting, profile: Profile) -> bool:
    extracted_location = (posting.extracted_location or "").lower()
    remote = (posting.remote or "").lower()
    if not extracted_location and not remote:
        # extraction didn't give us a usable location signal; fall back to the raw field
        return _location_matches(posting, profile)
    return (
        any(loc.lower() in extracted_location for loc in profile.locations)
        or _same_region(posting.extracted_location or "", profile)
        or (
            profile.remote_ok
            and remote in ("remote", "hybrid")
            and _remote_country_ok(
                f"{posting.extracted_location or ''} {posting.location}", profile
            )
        )
    )


def _structured_score(posting: Posting, profile: Profile) -> ScoreResult:
    text = _posting_text(posting)

    for keyword in profile.exclude_keywords:
        if keyword.lower() in text:
            return ScoreResult(score=0, reasons=[f"excluded: contains {keyword!r}"])

    if profile.locations and not _structured_location_matches(posting, profile):
        return ScoreResult(score=0, reasons=["excluded: location/remote mismatch"])

    if profile.seniority_targets and posting.seniority:
        targets = [t.lower() for t in profile.seniority_targets]
        if posting.seniority.lower() not in targets:
            reason = f"excluded: seniority {posting.seniority!r} not in {profile.seniority_targets}"
            return ScoreResult(score=0, reasons=[reason])

    if (
        posting.min_years is not None
        and posting.min_years > profile.years_experience + 1
    ):
        reason = (
            f"excluded: requires {posting.min_years}+ years "
            f"(you have {profile.years_experience})"
        )
        return ScoreResult(score=0, reasons=[reason])

    # Score from the job's side: how much of what it asks for do you have? Every
    # skill counts equally, so profile weights don't apply here.
    required = {s.lower() for s in posting.required_skills}
    nice = {s.lower() for s in posting.nice_skills}
    mine = {skill.name: skill.name.lower() for skill in profile.skills}
    mine.update({tool: tool.lower() for tool in profile.tools})

    reasons = [f"{name} (required)" for name, low in mine.items() if low in required]
    reasons += [
        f"{name} (nice-to-have)"
        for name, low in mine.items()
        if low in nice and low not in required
    ]
    have = set(mine.values())
    required_share = (
        len(required & have) / max(len(required), MIN_LISTED_SKILLS)
        if required
        else None
    )
    nice_only = nice - required  # a skill listed as both counts as required
    nice_share = (
        len(nice_only & have) / max(len(nice_only), MIN_LISTED_SKILLS)
        if nice_only
        else None
    )

    if required_share is not None and nice_share is not None:
        share = REQUIRED_SHARE * required_share + (1 - REQUIRED_SHARE) * nice_share
    else:  # only one list: it counts for the whole score
        share = required_share if required_share is not None else nice_share or 0.0

    degree_fields = {d.lower() for d in posting.degree_fields}
    reasons += [major for major in profile.majors if major.lower() in degree_fields]
    return ScoreResult(
        score=round(100 * share),
        reasons=reasons or ["no profile skills/tools/majors matched"],
    )
