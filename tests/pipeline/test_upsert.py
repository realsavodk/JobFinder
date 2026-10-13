from datetime import UTC, datetime

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from job_scout.adapters.base import RawPosting
from job_scout.models import Posting
from job_scout.pipeline.upsert import upsert_postings


def _raw(title: str, *, description_html: str | None = "<p>desc</p>") -> RawPosting:
    return RawPosting(
        source="greenhouse",
        company="Stripe",
        external_id="123",
        title=title,
        location="Remote",
        url="https://stripe.com/jobs/123",
        description_html=description_html,
        posted_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_upsert_inserts_then_updates_in_place(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        upsert_postings(session, [_raw("Engineer")])
        rows = session.exec(select(Posting)).all()
        assert len(rows) == 1
        first_seen = rows[0].first_seen

        upsert_postings(session, [_raw("Senior Engineer")])
        rows = session.exec(select(Posting)).all()

    assert len(rows) == 1
    assert rows[0].title == "Senior Engineer"
    assert rows[0].first_seen == first_seen


def test_upsert_preserves_description_when_raw_value_is_none(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        upsert_postings(session, [_raw("Engineer")])
        upsert_postings(session, [_raw("Engineer", description_html=None)])
        row = session.exec(select(Posting)).one()

    assert row.description_html == "<p>desc</p>"


def test_upsert_rejects_new_posting_with_no_description(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)

    with (
        Session(engine) as session,
        pytest.raises(ValueError, match="description_html"),
    ):
        upsert_postings(session, [_raw("Engineer", description_html=None)])
