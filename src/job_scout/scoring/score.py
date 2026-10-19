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
NICE_SKILL_FACTOR = 0.5


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


def _location_matches(posting: Posting, profile: Profile) -> bool:
    location = posting.location.lower()
    if any(loc.lower() in location for loc in profile.locations):
        return True
    if _same_region(posting.location, profile):
        return True
    return profile.remote_ok and "remote" in location


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
        or (profile.remote_ok and remote in ("remote", "hybrid"))
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

    required = {s.lower() for s in posting.required_skills}
    nice = {s.lower() for s in posting.nice_skills}
    degree_fields = {d.lower() for d in posting.degree_fields}

    matched_weight = 0.0
    max_weight = 0.0
    reasons: list[str] = []

    for skill in profile.skills:
        max_weight += skill.weight
        name_lower = skill.name.lower()
        if name_lower in required:
            matched_weight += skill.weight
            reasons.append(f"{skill.name} (required)")
        elif name_lower in nice:
            matched_weight += skill.weight * NICE_SKILL_FACTOR
            reasons.append(f"{skill.name} (nice-to-have)")

    for tool in profile.tools:
        max_weight += TOOL_WEIGHT
        name_lower = tool.lower()
        if name_lower in required:
            matched_weight += TOOL_WEIGHT
            reasons.append(f"{tool} (required)")
        elif name_lower in nice:
            matched_weight += TOOL_WEIGHT * NICE_SKILL_FACTOR
            reasons.append(f"{tool} (nice-to-have)")

    for major in profile.majors:
        max_weight += MAJOR_WEIGHT
        if major.lower() in degree_fields:
            matched_weight += MAJOR_WEIGHT
            reasons.append(major)

    score = round(100 * matched_weight / max_weight) if max_weight else 0
    return ScoreResult(
        score=score, reasons=reasons or ["no profile skills/tools/majors matched"]
    )
