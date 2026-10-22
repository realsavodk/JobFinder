from __future__ import annotations

from collections.abc import Callable
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
    applied_facets: dict[str, list[str]] | None = None,
    known_external_ids: frozenset[str] = frozenset(),
    title_ok: Callable[[str], bool] = lambda _title: True,
) -> list[RawPosting]:
    base = f"https://{tenant}.{host}.myworkdayjobs.com"
    jobs = await _list_all_jobs(
        client, base, tenant, site, search_text, applied_facets or {}
    )

    postings = []
    # Each detail is its own request, so jobs with unwanted titles stop at the list.
    for job in (j for j in jobs if title_ok(j.get("title") or "")):
        external_id = _external_id(job)
        from_list = _from_list(
            job, company=company, external_id=external_id, base=base, site=site
        )
        if external_id in known_external_ids:
            postings.append(from_list)
            continue
        try:
            detail = await _fetch_detail(
                client, base, tenant, site, job["externalPath"]
            )
            postings.append(
                _from_detail(detail, company=company, external_id=external_id)
            )
        except (httpx.HTTPError, KeyError, ValueError):
            # One job's detail failing (often just closed) mustn't fail the company;
            # keep it from the list, without a description.
            postings.append(from_list)
    return postings


def _external_id(job: dict) -> str:
    # Most tenants put the requisition id in bulletFields; some omit it, but the
    # path ends in it ("/job/Toronto/Financial-Analyst_R2053481").
    if job.get("bulletFields"):
        return job["bulletFields"][0]
    return job["externalPath"].rsplit("_", 1)[-1]


async def _list_all_jobs(
    client: httpx.AsyncClient,
    base: str,
    tenant: str,
    site: str,
    search_text: str,
    applied_facets: dict[str, list[str]],
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
                "appliedFacets": applied_facets,
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
