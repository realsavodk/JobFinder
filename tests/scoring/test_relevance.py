import pytest

from job_scout.scoring.profile import Profile
from job_scout.scoring.score import is_relevant

ONTARIO_SWE = Profile(
    locations=["Ontario"],
    remote_ok=True,
    remote_country="Canada",
    title_keywords=["software", "developer", "full stack", "frontend", "front-end"],
)


@pytest.mark.parametrize(
    ("title", "location", "expected"),
    [
        ("Software Engineer, New Grad", "Toronto", True),
        ("Junior Developer", "Ottawa, ON", True),
        ("Full Stack Engineer", "Waterloo, Ontario, Canada", True),
        ("Front-End Engineer", "Remote - Canada", True),
        ("Software Engineer", "Seattle, WA", False),  # wrong place
        ("Software Engineer", "Remote - US", False),  # remote, but not in Canada
        ("Account Executive", "Toronto", False),  # wrong kind of role
        ("Business Development Representative", "Toronto", False),  # not "developer"
        ("Software Engineer", "Vancouver, BC", False),
    ],
)
def test_is_relevant_needs_a_wanted_title_and_place(title, location, expected):
    assert is_relevant(title, location, ONTARIO_SWE) is expected


def test_empty_profile_keeps_everything():
    assert is_relevant("Account Executive", "Anywhere", Profile())


def test_remote_without_country_keeps_any_remote_job():
    profile = Profile(locations=["Ontario"], remote_ok=True)
    assert is_relevant("Software Engineer", "Remote - US", profile)
