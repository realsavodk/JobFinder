import asyncio

import pytest

from job_scout.llm.base import ExtractionError, LLMProvider
from job_scout.llm.extraction import PostingExtraction
from job_scout.llm.provider import extract_with_retry


class _FakeProvider(LLMProvider):
    def __init__(self, behaviors):
        self._behaviors = list(behaviors)
        self.calls = 0

    async def extract(self, posting_text: str) -> PostingExtraction:
        self.calls += 1
        behavior = self._behaviors.pop(0)
        if isinstance(behavior, Exception):
            raise behavior
        if behavior == "hang":
            await asyncio.sleep(10)
        return behavior

    async def check_available(self) -> tuple[bool, str]:
        return True, ""

    async def aclose(self) -> None:
        pass


def test_succeeds_on_first_attempt():
    expected = PostingExtraction(seniority="senior")
    provider = _FakeProvider([expected])

    result = asyncio.run(extract_with_retry(provider, "text"))

    assert result is expected
    assert provider.calls == 1


def test_succeeds_on_retry_after_first_failure():
    expected = PostingExtraction(seniority="mid")
    provider = _FakeProvider([RuntimeError("boom"), expected])

    result = asyncio.run(extract_with_retry(provider, "text"))

    assert result is expected
    assert provider.calls == 2


def test_raises_extraction_error_after_both_attempts_fail():
    provider = _FakeProvider([RuntimeError("boom"), RuntimeError("boom again")])

    with pytest.raises(ExtractionError):
        asyncio.run(extract_with_retry(provider, "text"))

    assert provider.calls == 2


def test_timeout_counts_as_a_failed_attempt(monkeypatch):
    import job_scout.llm.provider as provider_module

    monkeypatch.setattr(provider_module, "EXTRACTION_TIMEOUT_SECONDS", 0.05)
    expected = PostingExtraction(seniority="junior")
    provider = _FakeProvider(["hang", expected])

    result = asyncio.run(extract_with_retry(provider, "text"))

    assert result is expected
    assert provider.calls == 2
