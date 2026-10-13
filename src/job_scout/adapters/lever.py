from __future__ import annotations

from datetime import UTC, datetime

import httpx

from job_scout.adapters.base import RawPosting
from job_scout.http import request_json

POSTINGS_URL = "https://api.lever.co/v0/postings/{token}"


async def fetch(
    client: httpx.AsyncClient, *, company: str, token: str
) -> list[RawPosting]:
    response = await request_json(
        client, "GET", POSTINGS_URL.format(token=token), params={"mode": "json"}
    )
    postings = response.json()
    return [_to_raw_posting(posting, company=company) for posting in postings]


def _to_raw_posting(posting: dict, *, company: str) -> RawPosting:
    return RawPosting(
        source="lever",
        company=company,
        external_id=posting["id"],
        title=posting["text"],
        location=posting["categories"].get("location", ""),
        url=posting["hostedUrl"],
        description_html=posting["description"],
        posted_at=datetime.fromtimestamp(posting["createdAt"] / 1000, tz=UTC),
        updated_at=None,
    )
