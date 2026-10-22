from datetime import UTC, datetime, timedelta

from sqlmodel import Session, SQLModel, create_engine, select

from job_scout.models import Posting
from job_scout.pipeline.expire import expire_postings


def _posting(external_id: str, *, last_seen: datetime, status: str = "new") -> Posting:
    now = datetime.now(UTC)
    return Posting(
        source="greenhouse",
        company="Stripe",
        external_id=external_id,
        title="Engineer",
        location="Remote",
        url=f"https://stripe.com/jobs/{external_id}",
        description_html="<p>desc</p>",
        first_seen=now,
        last_seen=last_seen,
        status=status,
    )


def test_expire_closes_only_stale_new_postings(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    run_started_at = datetime.now(UTC)
    stale = run_started_at - timedelta(hours=1)
    fresh = run_started_at + timedelta(seconds=1)

    with Session(engine) as session:
        session.add(_posting("stale-new", last_seen=stale, status="new"))
        session.add(_posting("stale-applied", last_seen=stale, status="applied"))
        session.add(_posting("fresh", last_seen=fresh, status="new"))
        session.commit()

        closed_count = expire_postings(
            session,
            source="greenhouse",
            company="Stripe",
            run_started_at=run_started_at,
        )

        rows = {p.external_id: p.status for p in session.exec(select(Posting)).all()}

    assert closed_count == 1
    assert rows["stale-new"] == "closed"
    assert rows["stale-applied"] == "applied"  # user tracking state preserved
    assert rows["fresh"] == "new"


def test_prune_deletes_only_untouched_postings_that_fail_the_profile(tmp_path):
    from datetime import date

    from job_scout.pipeline.expire import prune_irrelevant
    from job_scout.scoring.profile import Profile

    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    now = datetime.now(UTC)
    profile = Profile(locations=["Ontario"], title_keywords=["software"])

    def add(session, external_id, title, location, **extra):
        posting = _posting(external_id, last_seen=now, **extra)
        posting.title, posting.location = title, location
        session.add(posting)

    with Session(engine) as session:
        add(session, "keep-relevant", "Software Engineer", "Toronto")
        add(session, "drop", "Account Executive", "Toronto")
        add(session, "keep-applied", "Account Executive", "Toronto", status="applied")
        add(session, "keep-notes", "Account Executive", "Seattle, WA")
        session.commit()
        notes = session.exec(
            select(Posting).where(Posting.external_id == "keep-notes")
        ).one()
        notes.notes = "recruiter reached out"
        add(session, "keep-follow-up", "Account Executive", "Toronto")
        session.commit()
        follow = session.exec(
            select(Posting).where(Posting.external_id == "keep-follow-up")
        ).one()
        follow.follow_up_date = date.today()
        session.commit()

        removed = prune_irrelevant(
            session, source="greenhouse", company="Stripe", profile=profile
        )
        left = {p.external_id for p in session.exec(select(Posting))}

    assert removed == 1
    assert left == {"keep-relevant", "keep-applied", "keep-notes", "keep-follow-up"}
