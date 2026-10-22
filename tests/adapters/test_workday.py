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


def test_fetch_sends_region_filter_and_skips_details_for_unwanted_titles(monkeypatch):
    monkeypatch.setattr(workday, "PAGE_SIZE", 2)
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/jobs"):
            body = json.loads(request.content)
            return httpx.Response(200, json=(PAGE0 if body["offset"] == 0 else PAGE1))
        return httpx.Response(200, json=DETAIL)

    facets = {"State__Region__Province": ["on-id"]}

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await workday.fetch(
                client,
                company="Workday",
                tenant="workday",
                host="wd5",
                site="Workday",
                applied_facets=facets,
                title_ok=lambda title: "Developer" in title,
            )

    postings = asyncio.run(run())

    lists = [r for r in requests if r.url.path.endswith("/jobs")]
    details = [r for r in requests if not r.url.path.endswith("/jobs")]
    assert all(json.loads(r.content)["appliedFacets"] == facets for r in lists)
    assert len(details) == 1  # only "...Developer Platform..." of the 4 listed jobs
    assert len(postings) == 1


def _run_fetch(handler):
    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await workday.fetch(
                client, company="Brookfield", tenant="b", host="wd3", site="s"
            )

    return asyncio.run(run())


def test_fetch_takes_the_id_from_the_path_when_bullet_fields_are_missing():
    listing = {
        "total": 1,
        "jobPostings": [
            {
                "title": "Financial Analyst",
                "externalPath": "/job/Toronto-Ontario/Financial-Analyst_R2053481",
                "locationsText": "Toronto, Ontario",
            }
        ],
    }

    def handler(request):
        if request.url.path.endswith("/jobs"):
            return httpx.Response(200, json=listing)
        return httpx.Response(200, json=DETAIL)

    (posting,) = _run_fetch(handler)
    assert posting.external_id == "R2053481"


def test_a_broken_job_detail_keeps_the_job_from_the_list(monkeypatch):
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(job_http.asyncio, "sleep", no_sleep)
    listing = {"total": 2, "jobPostings": PAGE0["jobPostings"]}
    details = iter(
        [
            httpx.Response(
                200, json={"jobPostingInfo": {"externalUrl": "x"}}
            ),  # no title
            httpx.Response(404),
        ]
    )

    def handler(request):
        if request.url.path.endswith("/jobs"):
            return httpx.Response(200, json=listing)
        return next(details)

    postings = _run_fetch(handler)
    assert [p.title for p in postings] == [j["title"] for j in PAGE0["jobPostings"]]
    assert all(p.description_html is None for p in postings)
