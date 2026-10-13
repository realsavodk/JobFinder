import asyncio
import json
from pathlib import Path

import httpx

from job_scout.adapters import greenhouse

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "greenhouse_jobs.json").read_text()
)


def test_fetch_parses_real_response_shape():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/boards/stripe/jobs"
        assert request.url.params["content"] == "true"
        return httpx.Response(200, json=FIXTURE)

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await greenhouse.fetch(client, company="Stripe", token="stripe")

    postings = asyncio.run(run())

    assert len(postings) == len(FIXTURE["jobs"])
    first, raw_first = postings[0], FIXTURE["jobs"][0]
    assert first.source == "greenhouse"
    assert first.company == "Stripe"
    assert first.external_id == str(raw_first["id"])
    assert first.title == raw_first["title"]
    assert first.location == raw_first["location"]["name"]
    assert first.url == raw_first["absolute_url"]
    assert first.description_html == raw_first["content"]
