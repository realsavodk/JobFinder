from __future__ import annotations

import asyncio
import time
from urllib.parse import urlparse

import httpx

USER_AGENT = "JobScout/0.1 (personal job board; contact squad1302@gmail.com)"
MIN_INTERVAL_SECONDS = 1.0
MAX_RETRIES = 3

_last_request_at: dict[str, float] = {}
_host_locks: dict[str, asyncio.Lock] = {}


def _lock_for(host: str) -> asyncio.Lock:
    if host not in _host_locks:
        _host_locks[host] = asyncio.Lock()
    return _host_locks[host]


async def _throttle(host: str) -> None:
    # One in-flight request per host at a time, spaced >= MIN_INTERVAL_SECONDS apart.
    async with _lock_for(host):
        wait = MIN_INTERVAL_SECONDS - (
            time.monotonic() - _last_request_at.get(host, 0.0)
        )
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request_at[host] = time.monotonic()


async def request_json(
    client: httpx.AsyncClient, method: str, url: str, **kwargs
) -> httpx.Response:
    host = urlparse(url).netloc
    headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}

    for attempt in range(1, MAX_RETRIES + 1):
        await _throttle(host)
        try:
            response = await client.request(method, url, headers=headers, **kwargs)
            response.raise_for_status()
            return response
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            # A 404 won't change on retry; only server errors and 429 might.
            client_error = (
                isinstance(exc, httpx.HTTPStatusError)
                and exc.response.status_code < 500
                and exc.response.status_code != 429
            )
            if client_error or attempt == MAX_RETRIES:
                raise
            await asyncio.sleep(2 ** (attempt - 1))
    raise AssertionError("unreachable")  # pragma: no cover
