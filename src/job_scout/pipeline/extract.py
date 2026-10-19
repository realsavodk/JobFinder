from __future__ import annotations

from collections.abc import Callable

from sqlmodel import Session, select

from job_scout.llm.base import ExtractionError, LLMProvider
from job_scout.llm.provider import extract_with_retry
from job_scout.llm.text import extraction_text, hash_text, truncate_for_context
from job_scout.models import Posting
from job_scout.scoring.profile import Profile
from job_scout.scoring.score import keyword_score

# One budget for the whole run (~3s each on a local GPU), spent on the
# best keyword matches first, so adding companies doesn't grow the backlog.
MAX_EXTRACTIONS_PER_RUN = 300


async def extract_postings(
    session: Session,
    *,
    companies: list[str],
    profile: Profile,
    provider: LLMProvider,
    num_ctx: int,
    on_progress: Callable[[int, int], None] | None = None,
) -> int:
    postings = session.exec(
        select(Posting).where(
            Posting.company.in_(companies),
            Posting.duplicate_of_id.is_(None),
            Posting.status != "closed",
        )
    ).all()
    has_keywords = bool(profile.skills or profile.tools or profile.majors)

    # Pre-filter with the cheap keyword scorer: a posting it hard-excludes (location,
    # seniority by title, years, exclude keywords) or that mentions none of the
    # profile's skills isn't worth an LLM call.
    candidates = []
    for posting in postings:
        stripped = extraction_text(
            posting.title, posting.location, posting.description_html
        )
        content_hash = hash_text(stripped)
        already_done = (
            posting.extraction_hash == content_hash
            and posting.extraction_status != "pending"
        )
        if already_done:
            continue
        cheap = keyword_score(posting, profile)
        if cheap.reasons[0].startswith("excluded:") or (
            has_keywords and cheap.score == 0
        ):
            continue
        candidates.append((cheap.score, posting, stripped, content_hash))
    candidates.sort(key=lambda c: c[0], reverse=True)
    todo = [c[1:] for c in candidates[:MAX_EXTRACTIONS_PER_RUN]]

    attempted = 0
    failed = 0
    for posting, stripped, content_hash in todo:
        if on_progress:
            on_progress(attempted, len(todo))
        attempted += 1
        text = truncate_for_context(stripped, num_ctx=num_ctx)
        try:
            result = await extract_with_retry(provider, text)
        except ExtractionError:
            posting.extraction_hash = content_hash
            posting.extraction_status = "failed"
            session.add(posting)
            failed += 1
            continue

        posting.extraction_hash = content_hash
        posting.extraction_status = "success"
        posting.seniority = result.seniority
        posting.min_years = result.min_years
        posting.required_skills = result.required_skills
        posting.nice_skills = result.nice_skills
        posting.degree_fields = result.degree_fields
        posting.extracted_location = result.location
        posting.remote = result.remote
        posting.role_family = result.role_family
        session.add(posting)

    session.commit()
    if failed:
        print(f"{failed} posting(s) failed extraction after retry")
    return attempted
