import asyncio

import httpx
import pytest

from job_scout import http as job_http


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    monkeypatch.setattr(job_http, "MIN_INTERVAL_SECONDS", 0.0)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(job_http.asyncio, "sleep", no_sleep)


def test_request_json_retries_on_failure_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500)
        return httpx.Response(200, json={"ok": True})

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await job_http.request_json(
                client, "GET", "https://example.com/jobs"
            )

    response = asyncio.run(run())
    assert response.json() == {"ok": True}
    assert calls["n"] == 2


def test_request_json_raises_after_max_retries():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            await job_http.request_json(client, "GET", "https://example.com/jobs")

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(run())
