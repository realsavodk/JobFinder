from __future__ import annotations

import re
from collections import defaultdict

from sqlmodel import Session, select

from job_scout.models import Posting

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def normalize_title(title: str) -> str:
    return _NON_ALNUM_RE.sub(" ", title.lower()).strip()


def _choose_canonical(group: list[Posting]) -> Posting:
    # Prefer a posting the user has already triaged (status changed or notes added)
    # so a later dedup pass can never hide someone's own tracking history.
    engaged = [p for p in group if p.status != "new" or p.notes]
    pool = engaged or group
    return min(pool, key=lambda p: p.first_seen)


def dedup_postings(session: Session, *, company: str) -> int:
    postings = session.exec(
        select(Posting).where(Posting.company == company, Posting.status != "closed")
    ).all()

    groups: dict[tuple[str, str], list[Posting]] = defaultdict(list)
    for posting in postings:
        key = (normalize_title(posting.title), posting.location.strip().lower())
        groups[key].append(posting)

    linked = 0
    for group in groups.values():
        if len(group) < 2:
            if group[0].duplicate_of_id is not None:
                group[0].duplicate_of_id = None
            continue

        canonical = _choose_canonical(group)
        for posting in group:
            new_value = None if posting.id == canonical.id else canonical.id
            if posting.duplicate_of_id != new_value:
                posting.duplicate_of_id = new_value
                if new_value is not None:
                    linked += 1

    session.commit()
    return linked
