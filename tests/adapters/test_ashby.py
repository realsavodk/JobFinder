import asyncio
import json
from pathlib import Path

import httpx

from job_scout.adapters import ashby

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "ashby_jobs.json").read_text()
)


def test_fetch_parses_real_response_shape():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/posting-api/job-board/notion"
        return httpx.Response(200, json=FIXTURE)

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await ashby.fetch(client, company="Notion", token="notion")

    postings = asyncio.run(run())

    assert len(postings) == len(FIXTURE["jobs"])
    first, raw_first = postings[0], FIXTURE["jobs"][0]
    assert first.source == "ashby"
    assert first.company == "Notion"
    assert first.external_id == raw_first["id"]
    assert first.title == raw_first["title"]
    assert first.location == raw_first["location"]
    assert first.url == raw_first["jobUrl"]
    assert first.description_html == raw_first["descriptionHtml"]
    assert first.posted_at is not None
    assert first.updated_at is None
