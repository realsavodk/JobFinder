import asyncio
import json

import httpx
import pytest

from job_scout.llm.base import ExtractionError
from job_scout.llm.extraction import PostingExtraction
from job_scout.llm.ollama_provider import OllamaProvider


def _run(coro):
    return asyncio.run(coro)


def test_extract_sends_schema_format_and_temperature_zero():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        extraction = PostingExtraction(
            seniority="senior", min_years=5, required_skills=["Python"]
        )
        return httpx.Response(
            200, json={"message": {"content": extraction.model_dump_json()}}
        )

    async def run():
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            model="qwen2.5:7b",
            num_ctx=8192,
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        try:
            return await provider.extract("some posting text")
        finally:
            await provider.aclose()

    result = _run(run())

    assert captured["body"]["model"] == "qwen2.5:7b"
    assert captured["body"]["options"]["temperature"] == 0
    assert captured["body"]["options"]["num_ctx"] == 8192
    sent_format = captured["body"]["format"]
    assert (
        sent_format["properties"] == PostingExtraction.model_json_schema()["properties"]
    )
    assert set(sent_format["required"]) == set(sent_format["properties"])
    assert result.seniority == "senior"
    assert result.min_years == 5
    assert result.required_skills == ["Python"]


def test_extract_raises_extraction_error_on_http_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async def run():
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            model="qwen2.5:7b",
            num_ctx=8192,
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        try:
            await provider.extract("text")
        finally:
            await provider.aclose()

    with pytest.raises(ExtractionError):
        _run(run())


def test_extract_raises_extraction_error_on_malformed_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": "not json"}})

    async def run():
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            model="qwen2.5:7b",
            num_ctx=8192,
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        try:
            await provider.extract("text")
        finally:
            await provider.aclose()

    with pytest.raises(ExtractionError):
        _run(run())


def test_check_available_true_when_model_present():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"model": "qwen2.5:7b"}]})

    async def run():
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            model="qwen2.5:7b",
            num_ctx=8192,
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        try:
            return await provider.check_available()
        finally:
            await provider.aclose()

    ok, reason = _run(run())
    assert ok is True
    assert reason == ""


def test_check_available_matches_model_without_explicit_tag():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"model": "qwen2.5:7b"}]})

    async def run():
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            model="qwen2.5",
            num_ctx=8192,
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        try:
            return await provider.check_available()
        finally:
            await provider.aclose()

    ok, _reason = _run(run())
    assert ok is True


def test_check_available_false_when_model_missing():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"model": "llama3:8b"}]})

    async def run():
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            model="qwen2.5:7b",
            num_ctx=8192,
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        try:
            return await provider.check_available()
        finally:
            await provider.aclose()

    ok, reason = _run(run())
    assert ok is False
    assert "qwen2.5:7b" in reason


def test_check_available_false_when_unreachable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    async def run():
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            model="qwen2.5:7b",
            num_ctx=8192,
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        try:
            return await provider.check_available()
        finally:
            await provider.aclose()

    ok, reason = _run(run())
    assert ok is False
    assert "not reachable" in reason
