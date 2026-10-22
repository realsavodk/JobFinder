from __future__ import annotations

from datetime import datetime

from sqlmodel import Session, select

from job_scout.models import Posting
from job_scout.scoring.profile import Profile
from job_scout.scoring.score import is_relevant


def expire_postings(
    session: Session, *, source: str, company: str, run_started_at: datetime
) -> int:
    # Only auto-close postings the user hasn't acted on yet ("new") — once someone
    # marks a posting interested/applied/rejected/hidden, a later close shouldn't
    # silently erase that tracking state.
    stale = session.exec(
        select(Posting).where(
            Posting.source == source,
            Posting.company == company,
            Posting.last_seen < run_started_at,
            Posting.status == "new",
        )
    ).all()
    for posting in stale:
        posting.status = "closed"
    session.commit()
    return len(stale)


def prune_irrelevant(
    session: Session, *, source: str, company: str, profile: Profile
) -> int:
    """Delete stored postings that no longer pass the profile's title/location
    filter and that the user never touched. Expiring them instead would fill the
    Archive with jobs the user never wanted to see."""
    untouched = session.exec(
        select(Posting).where(
            Posting.source == source,
            Posting.company == company,
            Posting.status == "new",
            Posting.notes.is_(None),
            Posting.follow_up_date.is_(None),
        )
    ).all()
    doomed = [p for p in untouched if not is_relevant(p.title, p.location, profile)]
    for posting in doomed:
        session.delete(posting)
    session.commit()
    return len(doomed)
