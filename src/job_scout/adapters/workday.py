from __future__ import annotations

from datetime import UTC, date, datetime

import httpx

from job_scout.adapters.base import RawPosting
from job_scout.http import request_json

PAGE_SIZE = 20


async def fetch(
    client: httpx.AsyncClient,
    *,
    company: str,
    tenant: str,
    host: str,
    site: str,
    search_text: str = "",
    known_external_ids: frozenset[str] = frozenset(),
) -> list[RawPosting]:
    base = f"https://{tenant}.{host}.myworkdayjobs.com"
    jobs = await _list_all_jobs(client, base, tenant, site, search_text)

    postings = []
    for job in jobs:
        external_id = job["bulletFields"][0]
        if external_id in known_external_ids:
            postings.append(
                _from_list(
                    job, company=company, external_id=external_id, base=base, site=site
                )
            )
            continue
        detail = await _fetch_detail(client, base, tenant, site, job["externalPath"])
        postings.append(_from_detail(detail, company=company, external_id=external_id))
    return postings


async def _list_all_jobs(
    client: httpx.AsyncClient, base: str, tenant: str, site: str, search_text: str
) -> list[dict]:
    jobs: list[dict] = []
    offset = 0
    total = None
    while total is None or offset < total:
        response = await request_json(
            client,
            "POST",
            f"{base}/wday/cxs/{tenant}/{site}/jobs",
            json={
                "limit": PAGE_SIZE,
                "offset": offset,
                "searchText": search_text,
                "appliedFacets": {},
            },
        )
        data = response.json()
        if total is None:
            # Only the first page's `total` is reliable on live tenants we've tested —
            # later pages report total=0 even with more results still to come.
            total = data["total"]
        jobs.extend(data["jobPostings"])
        offset += PAGE_SIZE
    return jobs


async def _fetch_detail(
    client: httpx.AsyncClient, base: str, tenant: str, site: str, external_path: str
) -> dict:
    response = await request_json(
        client, "GET", f"{base}/wday/cxs/{tenant}/{site}{external_path}"
    )
    return response.json()["jobPostingInfo"]


def _from_list(
    job: dict, *, company: str, external_id: str, base: str, site: str
) -> RawPosting:
    return RawPosting(
        source="workday",
        company=company,
        external_id=external_id,
        title=job["title"],
        location=job["locationsText"],
        url=f"{base}/{site}{job['externalPath']}",
        description_html=None,
        posted_at=None,
        updated_at=None,
    )


def _from_detail(detail: dict, *, company: str, external_id: str) -> RawPosting:
    return RawPosting(
        source="workday",
        company=company,
        external_id=external_id,
        title=detail["title"],
        location=detail["location"],
        url=detail["externalUrl"],
        description_html=detail["jobDescription"],
        posted_at=_parse_start_date(detail.get("startDate")),
        updated_at=None,
    )


def _parse_start_date(start_date: str | None) -> datetime | None:
    # startDate is a bare "YYYY-MM-DD" with no timezone; the DB column requires
    # timezone-aware datetimes (every other source supplies an offset), so pin UTC.
    if start_date is None:
        return None
    return datetime.combine(
        date.fromisoformat(start_date), datetime.min.time(), tzinfo=UTC
    )
