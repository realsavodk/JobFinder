from __future__ import annotations

from sqlmodel import Session, select

from job_scout.models import Posting
from job_scout.scoring.profile import Profile
from job_scout.scoring.score import score_posting


def score_postings_for_company(
    session: Session, *, company: str, profile: Profile
) -> None:
    postings = session.exec(
        select(Posting).where(
            Posting.company == company, Posting.duplicate_of_id.is_(None)
        )
    ).all()
    for posting in postings:
        result = score_posting(posting, profile)
        posting.score = result.score
        posting.score_reasons = result.reasons
    session.commit()
