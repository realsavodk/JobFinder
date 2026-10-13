from __future__ import annotations

import httpx

from job_scout.adapters.base import RawPosting
from job_scout.http import request_json

BOARD_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


async def fetch(
    client: httpx.AsyncClient, *, company: str, token: str
) -> list[RawPosting]:
    response = await request_json(
        client, "GET", BOARD_URL.format(token=token), params={"content": "true"}
    )
    jobs = response.json()["jobs"]
    return [_to_raw_posting(job, company=company) for job in jobs]


def _to_raw_posting(job: dict, *, company: str) -> RawPosting:
    return RawPosting(
        source="greenhouse",
        company=company,
        external_id=str(job["id"]),
        title=job["title"],
        location=job["location"]["name"],
        url=job["absolute_url"],
        description_html=job["content"],
        posted_at=job.get("first_published"),
        updated_at=job.get("updated_at"),
    )
