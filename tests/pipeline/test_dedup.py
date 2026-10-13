from datetime import UTC, datetime, timedelta

from sqlmodel import Session, SQLModel, create_engine, select

from job_scout.models import Posting
from job_scout.pipeline.dedup import dedup_postings


def _posting(
    *,
    source: str,
    external_id: str,
    title: str,
    location: str,
    first_seen: datetime,
    status: str = "new",
    notes: str | None = None,
) -> Posting:
    return Posting(
        source=source,
        company="Acme",
        external_id=external_id,
        title=title,
        location=location,
        url=f"https://acme.example/{source}/{external_id}",
        description_html="<p>desc</p>",
        first_seen=first_seen,
        last_seen=first_seen,
        status=status,
        notes=notes,
    )


def _engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    return engine


def test_dedups_same_company_across_different_ats_sources(tmp_path):
    engine = _engine(tmp_path)
    now = datetime.now(UTC)
    with Session(engine) as session:
        session.add(
            _posting(
                source="greenhouse",
                external_id="gh-1",
                title="Backend Engineer",
                location="Remote",
                first_seen=now,
            )
        )
        session.add(
            _posting(
                source="lever",
                external_id="lv-1",
                title="Backend Engineer",
                location="Remote",
                first_seen=now + timedelta(hours=1),
            )
        )
        session.commit()

        linked = dedup_postings(session, company="Acme")
        rows = {p.source: p for p in session.exec(select(Posting)).all()}

    assert linked == 1
    assert rows["greenhouse"].duplicate_of_id is None
    assert rows["lever"].duplicate_of_id == rows["greenhouse"].id


def test_different_title_or_location_not_linked(tmp_path):
    engine = _engine(tmp_path)
    now = datetime.now(UTC)
    with Session(engine) as session:
        session.add(
            _posting(
                source="greenhouse",
                external_id="gh-1",
                title="Backend Engineer",
                location="Remote",
                first_seen=now,
            )
        )
        session.add(
            _posting(
                source="lever",
                external_id="lv-1",
                title="Frontend Engineer",
                location="Remote",
                first_seen=now,
            )
        )
        session.commit()

        linked = dedup_postings(session, company="Acme")
        rows = session.exec(select(Posting)).all()

    assert linked == 0
    assert all(p.duplicate_of_id is None for p in rows)


def test_canonical_prefers_already_engaged_posting_over_earliest(tmp_path):
    engine = _engine(tmp_path)
    now = datetime.now(UTC)
    with Session(engine) as session:
        earliest_untouched = _posting(
            source="greenhouse",
            external_id="gh-1",
            title="Backend Engineer",
            location="Remote",
            first_seen=now,
            status="new",
        )
        later_engaged = _posting(
            source="lever",
            external_id="lv-1",
            title="Backend Engineer",
            location="Remote",
            first_seen=now + timedelta(hours=1),
            status="interested",
            notes="looks good",
        )
        session.add(earliest_untouched)
        session.add(later_engaged)
        session.commit()

        dedup_postings(session, company="Acme")
        rows = {p.source: p for p in session.exec(select(Posting)).all()}

    assert rows["lever"].duplicate_of_id is None  # engaged one is canonical
    assert rows["greenhouse"].duplicate_of_id == rows["lever"].id


def test_group_recomputation_clears_stale_duplicate_pointer(tmp_path):
    engine = _engine(tmp_path)
    now = datetime.now(UTC)
    with Session(engine) as session:
        canonical = _posting(
            source="greenhouse",
            external_id="gh-1",
            title="Backend Engineer",
            location="Remote",
            first_seen=now,
        )
        duplicate = _posting(
            source="lever",
            external_id="lv-1",
            title="Backend Engineer",
            location="Remote",
            first_seen=now + timedelta(hours=1),
        )
        session.add(canonical)
        session.add(duplicate)
        session.commit()
        dedup_postings(session, company="Acme")

        # canonical closes (e.g. expired) — it drops out of the non-closed query entirely
        rows = {p.source: p for p in session.exec(select(Posting)).all()}
        rows["greenhouse"].status = "closed"
        session.add(rows["greenhouse"])
        session.commit()

        dedup_postings(session, company="Acme")
        rows = {p.source: p for p in session.exec(select(Posting)).all()}

    # the only remaining open posting must no longer be hidden as a duplicate
    assert rows["lever"].duplicate_of_id is None
