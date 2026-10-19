from datetime import UTC, datetime

from job_scout.models import Posting
from job_scout.scoring.profile import Profile, SkillWeight
from job_scout.scoring.score import score_posting


def _posting(*, title: str, location: str, description_html: str) -> Posting:
    now = datetime.now(UTC)
    return Posting(
        source="greenhouse",
        company="Stripe",
        external_id="1",
        title=title,
        location=location,
        url="https://stripe.com/jobs/1",
        description_html=description_html,
        first_seen=now,
        last_seen=now,
    )


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
    }
    defaults.update(overrides)
    return Profile(**defaults)


def test_matching_skills_increase_score():
    posting = _posting(
        title="Backend Engineer",
        location="Remote",
        description_html="<p>We use Python and SQL daily, plus Docker.</p>",
    )
    result = score_posting(posting, _profile())

    # matched weight (Python 5 + SQL 3 + Docker 1) / max weight (+ unmatched major 2) = 9/11
    assert result.score == 82
    assert set(result.reasons) == {"Python", "SQL", "Docker"}


def test_no_keyword_matches_scores_zero_with_explanatory_reason():
    posting = _posting(
        title="Graphic Designer",
        location="Remote",
        description_html="<p>Adobe Illustrator and Figma experience needed.</p>",
    )
    result = score_posting(posting, _profile())

    assert result.score == 0
    assert result.reasons == ["no profile keywords matched"]


def test_exclude_keyword_hard_filters_to_zero_even_with_skill_matches():
    posting = _posting(
        title="Python Engineer",
        location="Remote",
        description_html="<p>Great Python role, unpaid internship.</p>",
    )
    result = score_posting(posting, _profile())

    assert result.score == 0
    assert "excluded" in result.reasons[0]


def test_location_mismatch_hard_filters_to_zero():
    posting = _posting(
        title="Python Engineer",
        location="Berlin, Germany",
        description_html="<p>Python and SQL role.</p>",
    )
    result = score_posting(posting, _profile(remote_ok=False))

    assert result.score == 0
    assert result.reasons == ["excluded: location/remote mismatch"]


def test_empty_locations_list_does_not_hard_filter():
    posting = _posting(
        title="Python Engineer",
        location="Berlin, Germany",
        description_html="<p>Python role.</p>",
    )
    result = score_posting(posting, _profile(locations=[], remote_ok=False))

    assert result.score > 0


def test_senior_title_hard_filters_for_fresh_graduate():
    posting = _posting(
        title="Senior Backend Engineer",
        location="Remote",
        description_html="<p>Python and SQL role.</p>",
    )
    result = score_posting(posting, _profile(years_experience=0))

    assert result.score == 0
    assert "excluded" in result.reasons[0]
    assert "years" in result.reasons[0]


def test_new_grad_title_scores_normally_for_fresh_graduate():
    posting = _posting(
        title="Software Engineer, New Grad",
        location="Remote",
        description_html="<p>Python and SQL role.</p>",
    )
    result = score_posting(posting, _profile(years_experience=0))

    assert result.score > 0


def test_explicit_years_requirement_in_description_hard_filters():
    posting = _posting(
        title="Software Engineer",
        location="Remote",
        description_html="<p>Requires 5+ years of experience. Python and SQL.</p>",
    )
    result = score_posting(posting, _profile(years_experience=1))

    assert result.score == 0
    assert "5" in result.reasons[0]


def test_years_requirement_within_plus_one_tolerance_is_not_filtered():
    posting = _posting(
        title="Software Engineer",
        location="Remote",
        description_html="<p>Minimum of 3 years of experience. Python and SQL.</p>",
    )
    result = score_posting(posting, _profile(years_experience=2))

    assert result.score > 0


def test_keyword_skills_match_whole_words_only():
    posting = _posting(
        title="Frontend Engineer",
        location="Remote",
        description_html="<p>Shape our JavaScript and C++ codebase.</p>",
    )
    profile = _profile(
        skills=[
            SkillWeight(name="C", weight=1),
            SkillWeight(name="Java", weight=1),
            SkillWeight(name="SHAP", weight=1),
            SkillWeight(name="JavaScript", weight=1),
            SkillWeight(name="C++", weight=1),
        ],
        tools=[],
        majors=[],
    )
    result = score_posting(posting, profile)

    assert result.reasons == ["JavaScript", "C++"]


def test_profile_province_matches_city_only_posting_location():
    profile = _profile(locations=["Ontario"], remote_ok=False)
    toronto = _posting(
        title="Backend Engineer", location="Toronto", description_html="<p>Python</p>"
    )
    seattle = _posting(
        title="Backend Engineer",
        location="Seattle, WA",
        description_html="<p>Python</p>",
    )

    assert score_posting(toronto, profile).score > 0
    assert score_posting(seattle, profile).reasons == [
        "excluded: location/remote mismatch"
    ]


def test_profile_city_widens_to_its_province():
    profile = _profile(locations=["Toronto"], remote_ok=False)
    ottawa = _posting(
        title="Backend Engineer",
        location="Ottawa, ON",
        description_html="<p>Python</p>",
    )
    assert score_posting(ottawa, profile).score > 0
