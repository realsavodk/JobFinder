from datetime import UTC, datetime

from job_scout.models import Posting
from job_scout.scoring.profile import Profile, SkillWeight
from job_scout.scoring.score import score_posting


def _posting(*, extraction_status: str = "success", **overrides) -> Posting:
    now = datetime.now(UTC)
    defaults = {
        "source": "greenhouse",
        "company": "Acme",
        "external_id": "1",
        "title": "Backend Engineer",
        "location": "Berlin, Germany",  # deliberately not matching profile.locations below
        "url": "https://acme.example/1",
        "description_html": "<p>desc</p>",
        "first_seen": now,
        "last_seen": now,
        "extraction_status": extraction_status,
    }
    defaults.update(overrides)
    return Posting(**defaults)


def _profile(**overrides) -> Profile:
    defaults = {
        "majors": ["Computer Science"],
        "skills": [
            SkillWeight(name="Python", weight=5),
            SkillWeight(name="SQL", weight=3),
        ],
        "tools": ["Docker"],
        "locations": ["Remote", "New York"],
        "remote_ok": True,
        "exclude_keywords": ["unpaid"],
        "years_experience": 3,
        "seniority_targets": [],
    }
    defaults.update(overrides)
    return Profile(**defaults)


def test_pending_extraction_falls_back_to_keyword_scoring():
    posting = _posting(
        extraction_status="pending",
        location="Remote",
        description_html="<p>Python and SQL role</p>",
    )
    result = score_posting(posting, _profile())
    assert result.score > 0  # keyword path matched Python/SQL in the raw text


def test_failed_extraction_falls_back_to_keyword_scoring():
    posting = _posting(
        extraction_status="failed",
        location="Remote",
        description_html="<p>Python and SQL role</p>",
    )
    result = score_posting(posting, _profile())
    assert result.score > 0


def test_structured_required_skill_outweighs_nice_skill():
    posting = _posting(
        remote="remote",
        required_skills=["Python"],
        nice_skills=["SQL"],
    )
    result = score_posting(posting, _profile())
    # Python required (5) + SQL nice (3*0.5=1.5) = 6.5 / max(5+3+1+2)=11 -> 59%
    assert result.score == 59
    assert "Python (required)" in result.reasons
    assert "SQL (nice-to-have)" in result.reasons


def test_structured_remote_field_satisfies_location_filter_even_with_mismatched_raw_location():
    posting = _posting(
        location="Berlin, Germany",  # raw field doesn't match profile.locations
        remote="remote",
        required_skills=["Python"],
    )
    result = score_posting(posting, _profile())
    assert result.score > 0


def test_structured_location_mismatch_excludes():
    posting = _posting(
        location="Berlin, Germany",
        extracted_location="Berlin, Germany",
        remote="onsite",
        required_skills=["Python"],
    )
    result = score_posting(posting, _profile(remote_ok=False))
    assert result.score == 0
    assert result.reasons == ["excluded: location/remote mismatch"]


def test_structured_seniority_mismatch_excludes():
    posting = _posting(remote="remote", seniority="senior", required_skills=["Python"])
    result = score_posting(posting, _profile(seniority_targets=["junior", "mid"]))
    assert result.score == 0
    assert "seniority" in result.reasons[0]


def test_structured_seniority_match_does_not_exclude():
    posting = _posting(remote="remote", seniority="mid", required_skills=["Python"])
    result = score_posting(posting, _profile(seniority_targets=["junior", "mid"]))
    assert result.score > 0


def test_structured_min_years_hard_filter():
    posting = _posting(remote="remote", min_years=8, required_skills=["Python"])
    result = score_posting(posting, _profile(years_experience=2))
    assert result.score == 0
    assert "8" in result.reasons[0]


def test_structured_min_years_within_tolerance_not_excluded():
    posting = _posting(remote="remote", min_years=3, required_skills=["Python"])
    result = score_posting(posting, _profile(years_experience=2))
    assert result.score > 0


def test_structured_degree_field_bonus():
    posting = _posting(
        remote="remote", required_skills=["Python"], degree_fields=["Computer Science"]
    )
    result = score_posting(posting, _profile())
    assert "Computer Science" in result.reasons


def test_structured_exclude_keyword_still_checked_against_raw_text():
    posting = _posting(
        remote="remote",
        description_html="<p>Great Python role, unpaid internship.</p>",
        required_skills=["Python"],
    )
    result = score_posting(posting, _profile())
    assert result.score == 0
    assert "excluded" in result.reasons[0]


def test_structured_no_matches_scores_zero_with_explanatory_reason():
    posting = _posting(remote="remote", required_skills=["Rust"], nice_skills=[])
    result = score_posting(posting, _profile())
    assert result.score == 0
    assert result.reasons == ["no profile skills/tools/majors matched"]
