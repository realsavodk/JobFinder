from __future__ import annotations

from datetime import datetime

from sqlmodel import Session, select

from job_scout.models import Posting


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
