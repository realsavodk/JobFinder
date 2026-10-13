from __future__ import annotations

import asyncio

from job_scout.config import LLMConfig
from job_scout.llm.base import ExtractionError, LLMProvider
from job_scout.llm.extraction import PostingExtraction
from job_scout.llm.ollama_provider import OllamaProvider
from job_scout.llm.openai_compatible_provider import OpenAICompatibleProvider

EXTRACTION_TIMEOUT_SECONDS = 120


def get_provider(llm_config: LLMConfig) -> LLMProvider:
    if llm_config.provider == "ollama":
        return OllamaProvider(
            base_url=llm_config.ollama_base_url,
            model=llm_config.model,
            num_ctx=llm_config.num_ctx,
        )
    if llm_config.provider == "openai_compatible":
        return OpenAICompatibleProvider(
            base_url=llm_config.base_url,
            api_key=llm_config.api_key,
            model=llm_config.model,
        )
    raise ValueError(f"unknown LLM_PROVIDER {llm_config.provider!r}")


async def extract_with_retry(provider: LLMProvider, text: str) -> PostingExtraction:
    last_error: Exception | None = None
    for _attempt in range(2):  # one try + one retry
        try:
            return await asyncio.wait_for(
                provider.extract(text), timeout=EXTRACTION_TIMEOUT_SECONDS
            )
        except Exception as exc:  # noqa: BLE001 - one bad posting must not kill the run
            last_error = exc
    raise ExtractionError(str(last_error))
