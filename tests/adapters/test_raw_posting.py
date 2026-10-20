from job_scout.adapters.base import RawPosting


def test_null_location_becomes_blank_instead_of_failing():
    posting = RawPosting(
        source="greenhouse",
        company="Acme",
        external_id="1",
        title="Engineer",
        location=None,
        url="https://example.com/1",
        description_html="",
        posted_at=None,
        updated_at=None,
    )
    assert posting.location == ""
