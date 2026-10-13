import pytest

from job_scout.config import LLMConfig
from job_scout.llm.ollama_provider import OllamaProvider
from job_scout.llm.openai_compatible_provider import OpenAICompatibleProvider
from job_scout.llm.provider import get_provider


def test_get_provider_ollama():
    provider = get_provider(
        LLMConfig(provider="ollama", model="qwen2.5:7b", num_ctx=4096)
    )
    assert isinstance(provider, OllamaProvider)
    assert provider.model == "qwen2.5:7b"
    assert provider.num_ctx == 4096


def test_get_provider_openai_compatible():
    provider = get_provider(
        LLMConfig(
            provider="openai_compatible",
            model="grok-test",
            base_url="https://api.x.ai/v1",
            api_key="key",
        )
    )
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.model == "grok-test"


def test_get_provider_unknown_raises():
    with pytest.raises(ValueError, match="unknown LLM_PROVIDER"):
        get_provider(LLMConfig(provider="not-a-real-provider"))
