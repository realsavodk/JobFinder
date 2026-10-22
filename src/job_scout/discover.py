"""Find companies' job boards in Common Crawl's URL index, then confirm which are live.

Everything is cached under the cache dir: downloaded index pages are never fetched
twice, and each company's check is reused for RECHECK_AFTER. Rerunning later only
downloads crawls published since the last run, and an interrupted run resumes.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yaml

from job_scout.http import USER_AGENT, request_json
from job_scout.regions import CA_PROVINCES, US_STATES, regions_in

CDX_ROOT = "https://index.commoncrawl.org"
QUERIES = {
    "greenhouse": ["url=job-boards.greenhouse.io/*", "url=boards.greenhouse.io/*"],
    "ashby": ["url=jobs.ashbyhq.com/*"],
    "lever": ["url=jobs.lever.co/*"],
    "workday": ["url=myworkdayjobs.com&matchType=domain"],
}
RECHECK_AFTER = timedelta(days=30)
CDX_ATTEMPTS = 6
CDX_TIMEOUT_SECONDS = 300
WORKDAY_WORKERS = 4  # each Workday tenant is its own host, so these don't share a limit

_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")
_SITE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
# "impl-wd5" hosts are Workday sandbox tenants, not real job boards.
_WORKDAY_HOST_RE = re.compile(r"^([a-z0-9_-]+)\.(wd\d+)\.myworkdayjobs\.com$")
_LOCALE_RE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")


def company_from_url(ats: str, url: str) -> dict | None:
    """The companies.yaml fields a job-board URL points at, or None."""
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if ats == "workday":
        host = _WORKDAY_HOST_RE.match(parsed.netloc.lower())
        if host is None:
            return None
        if parts and _LOCALE_RE.match(parts[0]):
            parts = parts[1:]
        if not parts or parts[0] == "wday" or not _SITE_RE.fullmatch(parts[0]):
            return None
        tenant, wd = host.groups()
        return {"ats": "workday", "tenant": tenant, "host": wd, "site": parts[0]}
    if not parts or not _SLUG_RE.fullmatch(parts[0].lower()):
        return None
    return {"ats": ats, "token": parts[0].lower()}


def company_key(config: dict) -> str:
    if config["ats"] == "workday":
        return f"workday:{config['tenant']}/{config['host']}/{config['site']}".lower()
    return f"{config['ats']}:{config['token']}"


def _complete_jsonl(text: str) -> bool:
    # A 504 page is HTML, and a download cut off by a timeout ends mid-line.
    if not text.strip():
        return False
    try:
        for line in text.splitlines():
            json.loads(line)
    except ValueError:
        return False
    return True


async def _cdx_get(client: httpx.AsyncClient, url: str, log) -> str | None:
    # The index is often overloaded; back off and keep requests one at a time.
    for attempt in range(1, CDX_ATTEMPTS + 1):
        try:
            response = await client.get(url, headers={"User-Agent": USER_AGENT})
            if response.status_code == 200 and _complete_jsonl(response.text):
                return response.text
            problem = f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            problem = type(exc).__name__
        if attempt < CDX_ATTEMPTS:
            log(f"    {problem}, retrying in {attempt * 15}s")
            await asyncio.sleep(attempt * 15)
    return None


async def find_candidates(
    client: httpx.AsyncClient,
    cache_dir: Path,
    crawls: list[str],
    log,
    cached_only: bool = False,
) -> tuple[dict[str, dict], int]:
    """Company configs seen in the given crawls, plus how many pages are missing.
    cached_only uses downloaded pages only, never waiting on the index."""
    candidates: dict[str, dict] = {}
    failed_pages = 0
    for crawl in crawls:
        for ats, queries in QUERIES.items():
            for i, query in enumerate(queries):
                base = cache_dir / crawl / f"{ats}-{i}"
                base.mkdir(parents=True, exist_ok=True)
                api = f"{CDX_ROOT}/{crawl}-index?{query}&output=json"

                count_file = base / "pages.json"
                if not count_file.exists() and cached_only:
                    failed_pages += 1
                    continue
                if not count_file.exists():
                    log(f"{crawl} {query}: counting pages")
                    text = await _cdx_get(client, f"{api}&showNumPages=true", log)
                    if text is None:
                        failed_pages += 1
                        continue
                    count_file.write_text(text)
                pages = json.loads(count_file.read_text())["pages"]

                for page in range(pages):
                    page_file = base / f"p{page}.jsonl"
                    if not page_file.exists() and cached_only:
                        failed_pages += 1
                        continue
                    if not page_file.exists():
                        log(f"{crawl} {query}: page {page + 1}/{pages}")
                        text = await _cdx_get(
                            client, f"{api}&fl=url&filter=status:200&page={page}", log
                        )
                        if text is None:
                            log("    gave up; it will be retried next run")
                            failed_pages += 1
                            continue
                        page_file.write_text(text)
                    for line in page_file.read_text().splitlines():
                        config = company_from_url(ats, json.loads(line)["url"])
                        if config:
                            candidates.setdefault(company_key(config), config)
    return candidates, failed_pages


def _job_locations(ats: str, job: dict) -> list[str]:
    if ats == "greenhouse":
        return [(job.get("location") or {}).get("name") or ""]
    if ats == "ashby":
        return [job.get("location") or ""] + [
            s.get("location") or "" for s in job.get("secondaryLocations") or []
        ]
    categories = job.get("categories") or {}  # lever
    return [categories.get("location") or "", *(categories.get("allLocations") or [])]


def _region_counts(ats: str, jobs: list[dict]) -> dict[str, int]:
    """How many jobs mention each state/province (a job counts once per region)."""
    counts: dict[str, int] = {}
    for job in jobs:
        for code in set().union(*(regions_in(loc) for loc in _job_locations(ats, job))):
            counts[code] = counts.get(code, 0) + 1
    return counts


_LOCATION_FACET_WORDS = ("location", "state", "region", "province", "city", "country")


def _facet_leaves(values: list[dict], param: str) -> list[tuple[str, dict]]:
    """(filter parameter, value) for every leaf. Some tenants nest groups
    ("Location Country" > "Canada"); a group's own facetParameter is what its
    children filter on."""
    leaves = []
    for value in values:
        children = value.get("values")
        if children:
            leaves += _facet_leaves(children, value.get("facetParameter") or param)
        else:
            leaves.append((param, value))
    return leaves


def _workday_regions(
    facets: list[dict],
) -> tuple[dict[str, int], dict[str, dict[str, list[str]]]]:
    """Region counts from Workday's location facets (they come back with any search),
    plus, per region, the appliedFacets filter that lists only that region's jobs.

    A job appears under several parameters (Ontario and Toronto), so each region
    takes its highest count across parameters rather than adding them up, and its
    filter comes from that same parameter.
    """
    by_param: dict[str, list[dict]] = {}
    for facet in facets:
        label = f"{facet.get('facetParameter', '')} {facet.get('descriptor', '')}"
        if not any(word in label.lower() for word in _LOCATION_FACET_WORDS):
            continue
        top = facet.get("facetParameter") or ""
        for param, value in _facet_leaves(facet.get("values") or [], top):
            by_param.setdefault(param, []).append(value)

    best: dict[str, int] = {}
    filters: dict[str, dict[str, list[str]]] = {}
    for param, values in by_param.items():
        counts: dict[str, int] = {}
        ids: dict[str, list[str]] = {}
        for value in values:
            for code in regions_in(value.get("descriptor") or ""):
                counts[code] = counts.get(code, 0) + int(value.get("count") or 0)
                if value.get("id"):
                    ids.setdefault(code, []).append(value["id"])
        for code, n in counts.items():
            if n > best.get(code, 0):
                best[code] = n
                filters[code] = {param: ids[code]} if ids.get(code) else {}
    return best, filters


async def check_company(client: httpx.AsyncClient, config: dict) -> dict | None:
    """{"jobs": n, "name": ..., "regions": {code: n}}; jobs=0 means dead, None means
    try again later."""
    ats = config["ats"]
    try:
        if ats == "workday":
            t, h, s = config["tenant"], config["host"], config["site"]
            response = await request_json(
                client,
                "POST",
                f"https://{t}.{h}.myworkdayjobs.com/wday/cxs/{t}/{s}/jobs",
                json={"limit": 1, "offset": 0, "searchText": "", "appliedFacets": {}},
            )
            data = response.json()
            regions, region_facets = _workday_regions(data.get("facets") or [])
            return {
                "jobs": int(data.get("total") or 0),
                "name": t,
                "regions": regions,
                "region_facets": region_facets,
            }
        token = config["token"]
        url = {
            "greenhouse": f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
            "ashby": f"https://api.ashbyhq.com/posting-api/job-board/{token}",
            "lever": f"https://api.lever.co/v0/postings/{token}?mode=json",
        }[ats]
        data = (await request_json(client, "GET", url)).json()
        jobs = data if ats == "lever" else data.get("jobs", [])
        name = jobs[0].get("company_name") if ats == "greenhouse" and jobs else None
        return {
            "jobs": len(jobs),
            "name": name or token,
            "regions": _region_counts(ats, jobs),
        }
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status < 500 and status != 429:
            return {"jobs": 0, "name": "", "regions": {}}  # no such board
        return None
    except (httpx.HTTPError, ValueError, AttributeError, TypeError):
        return None


def _stale(entry: dict | None, now: datetime) -> bool:
    return (
        entry is None
        # Checked before region counts existed (Workday got them later).
        or "regions" not in entry
        or entry["regions"] is None
        # Live Workday boards checked before their location filters were recorded.
        or (
            entry.get("config", {}).get("ats") == "workday"
            and entry.get("jobs", 0) > 0
            and "region_facets" not in entry
        )
        or now - datetime.fromisoformat(entry["checked_at"]) > RECHECK_AFTER
    )


async def check_all(
    client: httpx.AsyncClient,
    candidates: dict[str, dict],
    checked: dict[str, dict],
    save,
    log,
) -> None:
    now = datetime.now(UTC)
    todo = [k for k in candidates if _stale(checked.get(k), now)]
    log(
        f"Checking {len(todo)} companies ({len(candidates) - len(todo)} checked recently)"
    )
    queues: dict[str, asyncio.Queue] = {}
    for key in todo:
        queues.setdefault(candidates[key]["ats"], asyncio.Queue()).put_nowait(key)
    done = 0

    async def worker(queue: asyncio.Queue) -> None:
        nonlocal done
        while not queue.empty():
            key = queue.get_nowait()
            result = await check_company(client, candidates[key])
            if result is not None:
                checked[key] = {
                    "config": candidates[key],
                    **result,
                    "checked_at": datetime.now(UTC).isoformat(),
                }
            done += 1
            if done % 50 == 0:
                save()
                log(f"  checked {done}/{len(todo)}")

    workers = [
        worker(queue)
        for ats, queue in queues.items()
        for _ in range(WORKDAY_WORKERS if ats == "workday" else 1)
    ]
    try:
        await asyncio.gather(*workers)
    finally:
        save()


def _unique_names(entries: list[dict]) -> list[dict]:
    # A Workday tenant can run several boards ("bmo" external + Campus). The pipeline
    # keys expiry and run status by company name, so each board needs its own.
    counts: dict[str, int] = {}
    for e in entries:
        counts[e["name"]] = counts.get(e["name"], 0) + 1
    for e in entries:
        if counts[e["name"]] > 1 and e["ats"] == "workday":
            e["name"] = f"{e['name']} ({e['site']})"
    return entries


def live_entries(checked: dict[str, dict], region: str | None = None) -> list[dict]:
    """companies.yaml entries for live boards, biggest first within each job board.
    With a region, only boards with jobs there, ranked by how many they have in it."""
    live = [e for e in checked.values() if e["jobs"] > 0]
    if region:
        live = [e for e in live if (e.get("regions") or {}).get(region, 0) > 0]
        live.sort(key=lambda e: (e["config"]["ats"], -e["regions"][region]))
        entries = []
        for e in live:
            entry = {"name": e["name"], **e["config"]}
            # Workday: list only this region's jobs, so a fetch skips the rest.
            facet_filter = (e.get("region_facets") or {}).get(region)
            if facet_filter:
                entry["appliedFacets"] = facet_filter
            entry |= {
                "jobs": e["jobs"],
                f"jobs_in_{region.lower()}": e["regions"][region],
            }
            entries.append(entry)
        return _unique_names(entries)
    live.sort(key=lambda e: (e["config"]["ats"], -e["jobs"]))
    return _unique_names(
        [{"name": e["name"], **e["config"], "jobs": e["jobs"]} for e in live]
    )


async def run_discover(
    crawl_count: int,
    out: Path,
    cache_dir: Path,
    region: str | None = None,
    log=print,
    cached_only: bool = False,
) -> None:
    if region and region not in US_STATES and region not in CA_PROVINCES:
        raise SystemExit(f"unknown region {region!r}; use a code like ON or CA")
    cache_dir.mkdir(parents=True, exist_ok=True)
    checked_file = cache_dir / "checked.json"
    checked = json.loads(checked_file.read_text()) if checked_file.exists() else {}

    def save() -> None:
        tmp = checked_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(checked))
        tmp.replace(checked_file)  # never leave a half-written cache behind

    async with httpx.AsyncClient(timeout=CDX_TIMEOUT_SECONDS) as cdx:
        if cached_only:  # crawl ids from the cache, so the index isn't touched at all
            cached = [d.name for d in cache_dir.iterdir() if d.name.startswith("CC-")]
            crawls = sorted(cached, reverse=True)[:crawl_count]
        else:
            index = (await cdx.get(f"{CDX_ROOT}/collinfo.json")).json()
            crawls = [c["id"] for c in index[:crawl_count]]
        if not crawls:
            raise SystemExit("no cached crawls; run without --cached-only first")
        log(f"Searching {len(crawls)} crawls: {crawls[-1]} to {crawls[0]}")
        candidates, failed = await find_candidates(
            cdx, cache_dir, crawls, log, cached_only
        )
    by_ats = {}
    for config in candidates.values():
        by_ats[config["ats"]] = by_ats.get(config["ats"], 0) + 1
    log(f"Found {len(candidates)} candidate companies: {by_ats}")

    async with httpx.AsyncClient(timeout=30) as client:
        await check_all(client, candidates, checked, save, log)

    entries = live_entries(checked, region)
    where = f" with open jobs in {region}" if region else ""
    header = (
        f"# Generated by `job-scout discover` on {datetime.now(UTC):%Y-%m-%d}: "
        f"{len(entries)} live job boards{where}.\n"
        "# Copy the companies you want into companies.yaml. `jobs` is the open-job\n"
        "# count when checked; Workday fetches each job separately, so big Workday\n"
        "# boards are slow unless you add a searchText.\n"
    )
    out.write_text(
        header + yaml.safe_dump(entries, sort_keys=False, allow_unicode=True)
    )
    live_by_ats = {}
    for e in entries:
        live_by_ats[e["ats"]] = live_by_ats.get(e["ats"], 0) + 1
    log(f"Wrote {len(entries)} live companies to {out}: {live_by_ats}")
    if failed:
        log(
            f"{failed} index page(s) missing; a run without --cached-only fills them in."
        )
