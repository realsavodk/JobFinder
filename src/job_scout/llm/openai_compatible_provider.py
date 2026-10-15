from __future__ import annotations

import httpx
from openai import APIError, AsyncOpenAI
from pydantic import BaseModel

from job_scout.llm.base import ExtractionError, LLMProvider
from job_scout.llm.extraction import EXTRACTION_SYSTEM_PROMPT, PostingExtraction
from job_scout.llm.resume import RESUME_SYSTEM_PROMPT, ResumeExtraction

_SCHEMA_HINT = (
    "\n\nRespond with a single JSON object matching exactly this schema "
    "(no prose, no markdown fences): {schema}"
)


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = model
        self._client = AsyncOpenAI(
            base_url=base_url, api_key=api_key, http_client=http_client
        )

    async def extract(self, posting_text: str) -> PostingExtraction:
        return await self._chat_json(
            EXTRACTION_SYSTEM_PROMPT, posting_text, PostingExtraction
        )

    async def extract_resume(self, resume_text: str) -> ResumeExtraction:
        return await self._chat_json(
            RESUME_SYSTEM_PROMPT, resume_text, ResumeExtraction
        )

    async def _chat_json[M: BaseModel](
        self, system_prompt: str, text: str, model_cls: type[M]
    ) -> M:
        system = system_prompt + _SCHEMA_HINT.format(
            schema=model_cls.model_json_schema()
        )
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                temperature=0,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": text},
                ],
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            return model_cls.model_validate_json(content)
        except (APIError, ValueError) as exc:
            raise ExtractionError(str(exc)) from exc

    async def check_available(self) -> tuple[bool, str]:
        # Hosted API — not pre-flighted the way a local Ollama install is; real
        # failures surface per extraction call instead.
        return True, ""

    async def aclose(self) -> None:
        await self._client.close()
