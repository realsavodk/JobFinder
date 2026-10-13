import asyncio
import json
from pathlib import Path

import httpx

from job_scout.adapters import lever

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "lever_postings.json").read_text()
)


def test_fetch_parses_real_response_shape():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v0/postings/spotify"
        assert request.url.params["mode"] == "json"
        return httpx.Response(200, json=FIXTURE)

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await lever.fetch(client, company="Spotify", token="spotify")

    postings = asyncio.run(run())

    assert len(postings) == len(FIXTURE)
    first, raw_first = postings[0], FIXTURE[0]
    assert first.source == "lever"
    assert first.company == "Spotify"
    assert first.external_id == raw_first["id"]
    assert first.title == raw_first["text"]
    assert first.location == raw_first["categories"]["location"]
    assert first.url == raw_first["hostedUrl"]
    assert first.description_html == raw_first["description"]
    assert first.posted_at is not None
    assert first.updated_at is None
