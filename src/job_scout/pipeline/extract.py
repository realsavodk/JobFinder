from __future__ import annotations

from sqlmodel import Session, select

from job_scout.llm.base import ExtractionError, LLMProvider
from job_scout.llm.provider import extract_with_retry
from job_scout.llm.text import extraction_text, hash_text, truncate_for_context
from job_scout.models import Posting

MAX_EXTRACTIONS_PER_RUN = 50


async def extract_company_postings(
    session: Session, *, company: str, provider: LLMProvider, num_ctx: int
) -> int:
    postings = session.exec(
        select(Posting).where(
            Posting.company == company, Posting.duplicate_of_id.is_(None)
        )
    ).all()

    attempted = 0
    failed = 0
    for posting in postings:
        if attempted >= MAX_EXTRACTIONS_PER_RUN:
            break

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
        print(f"{company}: {failed} posting(s) failed extraction after retry")
    return attempted
