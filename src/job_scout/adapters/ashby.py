from __future__ import annotations

import httpx

from job_scout.adapters.base import RawPosting
from job_scout.http import request_json

JOB_BOARD_URL = "https://api.ashbyhq.com/posting-api/job-board/{token}"


async def fetch(
    client: httpx.AsyncClient, *, company: str, token: str
) -> list[RawPosting]:
    response = await request_json(client, "GET", JOB_BOARD_URL.format(token=token))
    jobs = response.json()["jobs"]
    return [_to_raw_posting(job, company=company) for job in jobs]


def _to_raw_posting(job: dict, *, company: str) -> RawPosting:
    return RawPosting(
        source="ashby",
        company=company,
        external_id=job["id"],
        title=job["title"],
        location=job["location"],
        url=job["jobUrl"],
        description_html=job["descriptionHtml"],
        posted_at=job.get("publishedAt"),
        updated_at=None,
    )
