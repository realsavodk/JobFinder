import asyncio
import json

import httpx
import pytest

from job_scout import discover
from job_scout import http as job_http
from job_scout.discover import (
    _complete_jsonl,
    check_all,
    check_company,
    company_from_url,
    company_key,
    live_entries,
)


@pytest.fixture(autouse=True)
def _no_throttle(monkeypatch):
    monkeypatch.setattr(job_http, "MIN_INTERVAL_SECONDS", 0.0)


@pytest.mark.parametrize(
    ("ats", "url", "expected"),
    [
        (
            "greenhouse",
            "https://job-boards.greenhouse.io/Stripe/jobs/123?gh_src=x",
            {"ats": "greenhouse", "token": "stripe"},
        ),
        (
            "ashby",
            "https://jobs.ashbyhq.com/notion/3f1c/application",
            {"ats": "ashby", "token": "notion"},
        ),
        ("lever", "https://jobs.lever.co/robots.txt", None),
        ("greenhouse", "https://job-boards.greenhouse.io/1pyra)mid_health&care", None),
        (
            "workday",
            "https://2020companies.wd1.myworkdayjobs.com/en-US/External_Careers/job/x",
            {
                "ats": "workday",
                "tenant": "2020companies",
                "host": "wd1",
                "site": "External_Careers",
            },
        ),
        (
            "workday",
            "https://acme.wd5.myworkdayjobs.com/Careers/details/y",
            {"ats": "workday", "tenant": "acme", "host": "wd5", "site": "Careers"},
        ),
        ("workday", "https://randstad.impl-wd502.myworkdayjobs.com/en-US/X", None),
        ("workday", "https://acme.wd5.myworkdayjobs.com/robots.txt", None),
        ("workday", "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/X/jobs", None),
    ],
)
def test_company_from_url(ats, url, expected):
    assert company_from_url(ats, url) == expected


def test_company_key_ignores_workday_site_case():
    a = {"ats": "workday", "tenant": "acme", "host": "wd5", "site": "Careers"}
    b = {**a, "site": "careers"}
    assert company_key(a) == company_key(b) == "workday:acme/wd5/careers"


def test_complete_jsonl_rejects_504_pages_and_cut_off_downloads():
    assert _complete_jsonl('{"url": "a"}\n{"url": "b"}\n')
    assert not _complete_jsonl("<html><title>504 Gateway Time-out</title></html>")
    assert not _complete_jsonl('{"url": "a"}\n{"url": "b')
    assert not _complete_jsonl("")


def _check(config, handler):
    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await check_company(client, config)

    return asyncio.run(run())


def test_check_greenhouse_counts_jobs_and_reads_company_name():
    def handler(request):
        assert request.url.path == "/v1/boards/brave/jobs"
        return httpx.Response(200, json={"jobs": [{"company_name": "Brave"}] * 3})

    result = _check({"ats": "greenhouse", "token": "brave"}, handler)
    assert result == {"jobs": 3, "name": "Brave", "regions": {}}


def test_check_workday_reads_total_from_one_job_query():
    def handler(request):
        assert request.url.host == "acme.wd5.myworkdayjobs.com"
        assert json.loads(request.content)["limit"] == 1
        return httpx.Response(200, json={"total": 1200, "jobPostings": [{}]})

    config = {"ats": "workday", "tenant": "acme", "host": "wd5", "site": "Careers"}
    assert _check(config, handler) == {
        "jobs": 1200,
        "name": "acme",
        "regions": {},
        "region_facets": {},
    }


def test_check_treats_404_as_dead_and_server_errors_as_unknown(monkeypatch):
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(job_http.asyncio, "sleep", no_sleep)
    config = {"ats": "ashby", "token": "gone"}
    dead = {"jobs": 0, "name": "", "regions": {}}
    assert _check(config, lambda r: httpx.Response(404)) == dead
    assert _check(config, lambda r: httpx.Response(503)) is None


def test_check_all_skips_recent_checks_and_saves_results():
    candidates = {
        "lever:fresh": {"ats": "lever", "token": "fresh"},
        "lever:new": {"ats": "lever", "token": "new"},
    }
    recent = discover.datetime.now(discover.UTC).isoformat()
    checked = {
        "lever:fresh": {
            "config": candidates["lever:fresh"],
            "jobs": 2,
            "name": "fresh",
            "regions": {},
            "checked_at": recent,
        }
    }
    requested = []

    def handler(request):
        requested.append(request.url.path)
        return httpx.Response(200, json=[{}, {}, {}])

    saves = []

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            await check_all(
                client, candidates, checked, lambda: saves.append(1), lambda _m: None
            )

    asyncio.run(run())
    assert requested == ["/v0/postings/new"]
    assert checked["lever:new"]["jobs"] == 3
    assert saves  # saved at the end even with fewer than 50 checks


def test_live_entries_drops_dead_boards_and_sorts_biggest_first():
    checked = {
        "greenhouse:a": {
            "config": {"ats": "greenhouse", "token": "a"},
            "jobs": 2,
            "name": "A",
        },
        "greenhouse:b": {
            "config": {"ats": "greenhouse", "token": "b"},
            "jobs": 9,
            "name": "B",
        },
        "greenhouse:dead": {
            "config": {"ats": "greenhouse", "token": "dead"},
            "jobs": 0,
            "name": "",
        },
    }
    assert live_entries(checked) == [
        {"name": "B", "ats": "greenhouse", "token": "b", "jobs": 9},
        {"name": "A", "ats": "greenhouse", "token": "a", "jobs": 2},
    ]


def test_check_counts_jobs_per_region_from_each_boards_location_fields():
    greenhouse_jobs = [
        {"location": {"name": "Toronto, Ontario"}},
        {"location": {"name": "Toronto; Seattle, WA"}},
        {"location": {"name": "London"}},
    ]
    result = _check(
        {"ats": "greenhouse", "token": "x"},
        lambda r: httpx.Response(200, json={"jobs": greenhouse_jobs}),
    )
    assert result["regions"] == {"ON": 2, "WA": 1}

    ashby_jobs = [
        {"location": "New York", "secondaryLocations": [{"location": "Toronto"}]}
    ]
    result = _check(
        {"ats": "ashby", "token": "x"},
        lambda r: httpx.Response(200, json={"jobs": ashby_jobs}),
    )
    assert result["regions"] == {"NY": 1, "ON": 1}

    lever_jobs = [{"categories": {"location": "Vancouver, BC"}}]
    result = _check(
        {"ats": "lever", "token": "x"},
        lambda r: httpx.Response(200, json=lever_jobs),
    )
    assert result["regions"] == {"BC": 1}


def test_live_entries_for_a_region_keeps_only_boards_hiring_there():
    checked = {
        "greenhouse:a": {
            "config": {"ats": "greenhouse", "token": "a"},
            "jobs": 50,
            "name": "A",
            "regions": {"CA": 50},
        },
        "greenhouse:b": {
            "config": {"ats": "greenhouse", "token": "b"},
            "jobs": 10,
            "name": "B",
            "regions": {"ON": 3},
        },
        "workday:c": {
            "config": {"ats": "workday", "tenant": "c", "host": "wd1", "site": "S"},
            "jobs": 99,
            "name": "c",
            "regions": None,
        },
    }
    assert live_entries(checked, "ON") == [
        {"name": "B", "ats": "greenhouse", "token": "b", "jobs": 10, "jobs_in_on": 3}
    ]


def test_entries_checked_before_region_counts_are_rechecked():
    recent = discover.datetime.now(discover.UTC)
    old_style = {"jobs": 5, "name": "x", "checked_at": recent.isoformat()}
    assert discover._stale(old_style, recent)
    assert not discover._stale({**old_style, "regions": {}}, recent)


def test_workday_regions_come_from_location_facets():
    facets = [
        {  # not a location facet: "IT" mustn't be read as anything
            "facetParameter": "jobFamilyGroup",
            "descriptor": "Job Category",
            "values": [{"descriptor": "Information Technology", "count": 9}],
        },
        {
            "facetParameter": "State__Region__Province",
            "descriptor": "State/ Region/ Province",
            "values": [
                {"descriptor": "Ontario", "count": 31, "id": "on-id"},
                {"descriptor": "New York", "count": 5, "id": "ny-id"},
            ],
        },
        {  # same jobs again by city: take the higher count, don't add
            "facetParameter": "City",
            "descriptor": "City",
            "values": [
                {"descriptor": "Toronto, ON", "count": 30, "id": "tor-id"},
                {"descriptor": "Ottawa, ON", "count": 2, "id": "ott-id"},
            ],
        },
        {  # grouped locations: only the leaves count
            "facetParameter": "locationMainGroup",
            "descriptor": "Locations",
            "values": [
                {
                    "descriptor": "Location City",
                    "facetParameter": "locationCity",
                    "values": [
                        {"descriptor": "Vancouver, BC", "count": 4, "id": "van-id"}
                    ],
                }
            ],
        },
    ]

    def handler(request):
        return httpx.Response(200, json={"total": 50, "facets": facets})

    config = {"ats": "workday", "tenant": "cibc", "host": "wd3", "site": "campus"}
    result = _check(config, handler)
    assert result["regions"] == {"ON": 32, "NY": 5, "BC": 4}
    # Each region's filter comes from the parameter that gave its count; nested
    # groups filter on their own parameter.
    assert result["region_facets"] == {
        "ON": {"City": ["tor-id", "ott-id"]},
        "NY": {"State__Region__Province": ["ny-id"]},
        "BC": {"locationCity": ["van-id"]},
    }


def test_workday_entries_checked_without_regions_are_rechecked():
    now = discover.datetime.now(discover.UTC)
    entry = {"jobs": 5, "name": "x", "regions": None, "checked_at": now.isoformat()}
    assert discover._stale(entry, now)


def test_find_candidates_cached_only_never_calls_the_index(tmp_path):
    crawl = tmp_path / "CC-MAIN-2026-39" / "greenhouse-0"
    crawl.mkdir(parents=True)
    (crawl / "pages.json").write_text('{"pages": 2}')
    (crawl / "p0.jsonl").write_text(
        '{"url": "https://job-boards.greenhouse.io/stripe/jobs/1"}\n'
    )  # p1 was never downloaded

    def handler(request):
        raise AssertionError(f"index was called: {request.url}")

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await discover.find_candidates(
                client, tmp_path, ["CC-MAIN-2026-39"], lambda _m: None, cached_only=True
            )

    candidates, missing = asyncio.run(run())
    assert candidates == {"greenhouse:stripe": {"ats": "greenhouse", "token": "stripe"}}
    # p1 plus every other job board's query, none of which are cached here
    assert missing == 1 + sum(len(q) for q in discover.QUERIES.values()) - 1


def test_live_entries_add_the_workday_region_filter():
    checked = {
        "workday:cibc/wd3/campus": {
            "config": {
                "ats": "workday",
                "tenant": "cibc",
                "host": "wd3",
                "site": "campus",
            },
            "jobs": 44,
            "name": "cibc",
            "regions": {"ON": 32},
            "region_facets": {"ON": {"State__Region__Province": ["on-id"]}},
        }
    }
    (entry,) = live_entries(checked, "ON")
    assert entry["appliedFacets"] == {"State__Region__Province": ["on-id"]}


def test_live_workday_entries_without_filters_are_rechecked():
    now = discover.datetime.now(discover.UTC).isoformat()
    base = {"name": "x", "regions": {"ON": 3}, "checked_at": now}
    workday = {**base, "config": {"ats": "workday"}, "jobs": 5}
    current = discover.datetime.fromisoformat(now)
    assert discover._stale(workday, current)
    assert not discover._stale({**workday, "region_facets": {}}, current)
    assert not discover._stale({**base, "config": {"ats": "lever"}, "jobs": 5}, current)


def test_live_entries_name_each_board_of_a_multi_board_workday_tenant():
    def board(site, jobs):
        return {
            "config": {"ats": "workday", "tenant": "bmo", "host": "wd3", "site": site},
            "jobs": jobs,
            "name": "bmo",
            "regions": {"ON": jobs},
        }

    checked = {"a": board("external", 9), "b": board("Campus", 3)}
    names = [e["name"] for e in live_entries(checked, "ON")]
    assert names == ["bmo (external)", "bmo (Campus)"]
