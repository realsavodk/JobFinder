import asyncio
import json
from pathlib import Path

import httpx
import pytest

from job_scout import http as job_http
from job_scout.adapters import workday

FIXTURES = Path(__file__).parent / "fixtures"
PAGE0 = json.loads((FIXTURES / "workday_jobs_page0.json").read_text())
PAGE1 = json.loads((FIXTURES / "workday_jobs_page1.json").read_text())
DETAIL = json.loads((FIXTURES / "workday_job_detail.json").read_text())


@pytest.fixture(autouse=True)
def _no_throttle(monkeypatch):
    monkeypatch.setattr(job_http, "MIN_INTERVAL_SECONDS", 0.0)


def test_fetch_paginates_and_fetches_detail_for_new_jobs(monkeypatch):
    monkeypatch.setattr(workday, "PAGE_SIZE", 2)
    calls = {"list": 0, "detail": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/jobs"):
            body = json.loads(request.content)
            calls["list"] += 1
            page = PAGE0 if body["offset"] == 0 else PAGE1
            return httpx.Response(200, json=page)
        calls["detail"] += 1
        return httpx.Response(200, json=DETAIL)

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await workday.fetch(
                client,
                company="Workday",
                tenant="workday",
                host="wd5",
                site="Workday",
                search_text="",
            )

    postings = asyncio.run(run())

    # PAGE0 reports total=4 (synthetic, see fixture); real tenants report total=0
    # on every page after the first, so pagination must key off the first page only.
    assert calls["list"] == 2
    assert len(postings) == 4
    assert calls["detail"] == 4  # none of these external_ids are known yet

    first = postings[0]
    assert first.source == "workday"
    assert first.external_id == "JR-0110721"
    assert first.description_html == DETAIL["jobPostingInfo"]["jobDescription"]
    assert first.url == DETAIL["jobPostingInfo"]["externalUrl"]
    assert first.posted_at is not None
    assert first.posted_at.tzinfo is not None  # DB column rejects naive datetimes


def test_fetch_skips_detail_for_already_known_jobs(monkeypatch):
    monkeypatch.setattr(workday, "PAGE_SIZE", 2)
    calls = {"detail": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/jobs"):
            body = json.loads(request.content)
            return httpx.Response(200, json=(PAGE0 if body["offset"] == 0 else PAGE1))
        calls["detail"] += 1
        return httpx.Response(200, json=DETAIL)

    known = frozenset({"JR-0110721", "JR-0109135", "JR-0109909", "JR-0110609"})

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await workday.fetch(
                client,
                company="Workday",
                tenant="workday",
                host="wd5",
                site="Workday",
                known_external_ids=known,
            )

    postings = asyncio.run(run())

    assert calls["detail"] == 0
    assert len(postings) == 4
    first = postings[0]
    assert first.description_html is None
    assert first.title == PAGE0["jobPostings"][0]["title"]
    assert first.url.endswith(PAGE0["jobPostings"][0]["externalPath"])
