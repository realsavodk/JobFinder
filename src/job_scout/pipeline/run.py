from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlmodel import Session, SQLModel, create_engine

from job_scout.adapters import ashby, greenhouse, lever, workday
from job_scout.adapters.base import RawPosting
from job_scout.config import (
    CompanyConfig,
    database_url,
    load_companies,
    load_llm_config,
)
from job_scout.llm.provider import get_provider
from job_scout.models import CompanyRunStatus
from job_scout.pipeline.dedup import dedup_postings
from job_scout.pipeline.expire import expire_postings
from job_scout.pipeline.extract import extract_company_postings
from job_scout.pipeline.score import score_postings_for_company
from job_scout.pipeline.upsert import existing_external_ids, upsert_postings
from job_scout.scoring.profile import load_profile

ADAPTERS = {
    "greenhouse": greenhouse.fetch,
    "lever": lever.fetch,
    "ashby": ashby.fetch,
}


@dataclass
class RunProgress:
    running: bool = False
    stage: str = ""
    done: int = 0
    total: int = 0


# Read by the dashboard's progress bar; one run at a time per process.
progress = RunProgress()


def _report(stage: str, done: int, total: int) -> None:
    progress.stage, progress.done, progress.total = stage, done, total


@dataclass
class RunResult:
    company: str
    ok: bool
    message: str


async def _fetch_company(
    client: httpx.AsyncClient, session: Session, company: CompanyConfig
) -> list[RawPosting] | None:
    if company.ats == "workday":
        known_ids = existing_external_ids(
            session, source="workday", company=company.name
        )
        return await workday.fetch(
            client,
            company=company.name,
            tenant=company.tenant,
            host=company.host,
            site=company.site,
            search_text=company.searchText,
            known_external_ids=known_ids,
        )
    adapter = ADAPTERS.get(company.ats)
    if adapter is None:
        return None
    return await adapter(client, company=company.name, token=company.token)


async def _run_company(
    client: httpx.AsyncClient,
    session: Session,
    company: CompanyConfig,
    run_started_at: datetime,
) -> RunResult:
    """Fetch + upsert + expire for one companies.yaml entry. Dedup/extract/score run
    later, once per distinct company *name* across all its sources — see run_all."""
    status = session.get(CompanyRunStatus, company.name) or CompanyRunStatus(
        company=company.name
    )
    try:
        postings = await _fetch_company(client, session, company)
    except Exception as exc:  # noqa: BLE001 - one bad company must not kill the run
        status.last_run_at = datetime.now(UTC)
        status.ok = False
        status.message = str(exc)
        session.add(status)
        session.commit()
        return RunResult(company=company.name, ok=False, message=status.message)

    if postings is None:
        status.last_run_at = datetime.now(UTC)
        status.ok = False
        status.message = f"no adapter for ats={company.ats!r}"
        session.add(status)
        session.commit()
        return RunResult(company=company.name, ok=False, message=status.message)

    upsert_postings(session, postings)
    closed = expire_postings(
        session,
        source=company.ats,
        company=company.name,
        run_started_at=run_started_at,
    )

    status.last_run_at = datetime.now(UTC)
    status.ok = True
    status.message = f"{len(postings)} postings, {closed} closed"
    status.postings_count = len(postings)
    status.closed_count = closed
    session.add(status)
    session.commit()
    return RunResult(company=company.name, ok=True, message=status.message)


async def run_all(companies_file: Path, profile_file: Path) -> list[RunResult]:
    companies = load_companies(companies_file)
    profile = load_profile(profile_file)
    llm_config = load_llm_config()
    provider = get_provider(llm_config)
    progress.running = True
    _report("Starting", 0, 0)

    try:
        extraction_ok, reason = await provider.check_available()
        if not extraction_ok:
            print(f"LLM extraction disabled: {reason}")

        engine = create_engine(database_url())
        SQLModel.metadata.create_all(engine)
        run_started_at = datetime.now(UTC)

        results: list[RunResult] = []
        touched_companies: list[str] = []
        async with httpx.AsyncClient(timeout=30) as client:
            with Session(engine) as session:
                for i, company in enumerate(companies):
                    _report(f"Fetching jobs from {company.name}", i, len(companies))
                    results.append(
                        await _run_company(client, session, company, run_started_at)
                    )
                    if company.name not in touched_companies:
                        touched_companies.append(company.name)

                # Cross-source: the same company name can span multiple
                # companies.yaml entries on different ATS adapters, so dedup,
                # extraction, and scoring run once per name across all sources,
                # not nested inside the per-entry loop above.
                for name in touched_companies:
                    dedup_postings(session, company=name)

                for i, name in enumerate(touched_companies):
                    label = f"{name} ({i + 1} of {len(touched_companies)} companies)"
                    if extraction_ok:
                        await extract_company_postings(
                            session,
                            company=name,
                            provider=provider,
                            num_ctx=llm_config.num_ctx,
                            on_progress=lambda done, total, label=label: _report(
                                f"Reading postings with the LLM: {label}", done, total
                            ),
                        )
                    _report(f"Scoring {label}", 0, 0)
                    score_postings_for_company(session, company=name, profile=profile)

        return results
    finally:
        progress.running = False
        _report("Done", 0, 0)
        await provider.aclose()
