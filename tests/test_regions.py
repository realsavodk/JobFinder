import pytest

from job_scout.regions import regions_in


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("San Francisco, California", {"CA"}),
        ("Seattle, WA", {"WA"}),
        ("USA.VA.Reston", {"VA"}),
        ("Canada, BC, Vancouver", {"BC"}),
        ("Toronto", {"ON"}),
        ("SF, SEA, NYC, CHI, ATL", {"CA", "WA", "NY", "IL", "GA"}),
        ("New York City, Washington DC, Remote", {"NY", "DC"}),
        ("Seattle, Washington, United States", {"WA"}),
        ("Indianapolis, IN", {"IN"}),
        ("Portland, OR", {"OR"}),
        # Uppercase English words and Canada's "CA" must not become states.
        ("Dublin OR London", set()),
        ("IN - Bengaluru", set()),
        ("Toronto, CA", {"ON"}),
        ("CA-Toronto; CA-Remote", {"ON"}),
        ("London", set()),
        ("Mexico City", set()),
        ("US-Remote", set()),
    ],
)
def test_regions_in(location, expected):
    assert regions_in(location) == expected
