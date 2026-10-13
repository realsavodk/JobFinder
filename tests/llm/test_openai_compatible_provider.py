import asyncio

import httpx
import pytest

from job_scout.llm.base import ExtractionError
from job_scout.llm.openai_compatible_provider import OpenAICompatibleProvider


def _provider_with_handler(handler):
    return OpenAICompatibleProvider(
        base_url="https://api.x.ai/v1",
        api_key="test-key",
        model="grok-test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def test_extract_parses_json_object_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "1",
                "object": "chat.completion",
                "created": 0,
                "model": "grok-test",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": '{"seniority": "mid", "min_years": 2, "required_skills": ["SQL"]}',
                        },
                    }
                ],
            },
        )

    async def run():
        provider = _provider_with_handler(handler)
        try:
            return await provider.extract("some posting text")
        finally:
            await provider.aclose()

    result = asyncio.run(run())
    assert result.seniority == "mid"
    assert result.min_years == 2
    assert result.required_skills == ["SQL"]


def test_extract_raises_extraction_error_on_malformed_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "1",
                "object": "chat.completion",
                "created": 0,
                "model": "grok-test",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "not json"},
                    }
                ],
            },
        )

    async def run():
        provider = _provider_with_handler(handler)
        try:
            await provider.extract("text")
        finally:
            await provider.aclose()

    with pytest.raises(ExtractionError):
        asyncio.run(run())


def test_check_available_is_always_true():
    async def run():
        provider = OpenAICompatibleProvider(
            base_url="https://api.x.ai/v1", api_key="test-key", model="grok-test"
        )
        try:
            return await provider.check_available()
        finally:
            await provider.aclose()

    ok, reason = asyncio.run(run())
    assert ok is True
    assert reason == ""
