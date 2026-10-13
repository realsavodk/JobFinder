from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Session, select

from job_scout.adapters.base import RawPosting
from job_scout.models import Posting

_MUTABLE_FIELDS = (
    "title",
    "location",
    "url",
    "description_html",
    "posted_at",
    "updated_at",
)


def existing_external_ids(
    session: Session, *, source: str, company: str
) -> frozenset[str]:
    rows = session.exec(
        select(Posting.external_id).where(
            Posting.source == source, Posting.company == company
        )
    ).all()
    return frozenset(rows)


def upsert_postings(session: Session, postings: list[RawPosting]) -> None:
    now = datetime.now(UTC)
    for raw in postings:
        existing = session.exec(
            select(Posting).where(
                Posting.source == raw.source,
                Posting.company == raw.company,
                Posting.external_id == raw.external_id,
            )
        ).first()
        if existing is None:
            if raw.description_html is None:
                raise ValueError(
                    f"new posting {raw.source}/{raw.company}/{raw.external_id} "
                    "has no description_html (adapter skipped detail fetch on a first-seen posting)"
                )
            session.add(Posting(**raw.model_dump(), first_seen=now, last_seen=now))
            continue
        for field in _MUTABLE_FIELDS:
            value = getattr(raw, field)
            if value is not None:
                setattr(existing, field, value)
        existing.last_seen = now
    session.commit()
