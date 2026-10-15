from __future__ import annotations

import httpx
from pydantic import BaseModel

from job_scout.llm.base import ExtractionError, LLMProvider
from job_scout.llm.extraction import EXTRACTION_SYSTEM_PROMPT, PostingExtraction
from job_scout.llm.resume import RESUME_SYSTEM_PROMPT, ResumeExtraction


class OllamaProvider(LLMProvider):
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        num_ctx: int,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.num_ctx = num_ctx
        # No httpx timeout: extract_with_retry's EXTRACTION_TIMEOUT_SECONDS governs,
        # and httpx's 5s default aborts model loads.
        self._client = client or httpx.AsyncClient(timeout=None)

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
        schema = model_cls.model_json_schema()
        # Fields with defaults aren't "required", so Ollama's grammar lets the model skip them.
        schema["required"] = list(schema["properties"])
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            "stream": False,
            "think": False,  # qwen3 thinks by default: ~3x slower, and format already constrains output
            "format": schema,
            "options": {"temperature": 0, "num_ctx": self.num_ctx},
        }
        try:
            response = await self._client.post(
                f"{self.base_url}/api/chat", json=payload
            )
            response.raise_for_status()
            content = response.json()["message"]["content"]
            return model_cls.model_validate_json(content)
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise ExtractionError(str(exc)) from exc

    async def check_available(self) -> tuple[bool, str]:
        try:
            response = await self._client.get(f"{self.base_url}/api/tags", timeout=5)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return False, f"Ollama not reachable at {self.base_url}: {exc}"

        names = {
            m.get("model") or m.get("name") for m in response.json().get("models", [])
        }
        if not any(
            name == self.model or (name and name.startswith(f"{self.model}:"))
            for name in names
        ):
            return False, (
                f"model {self.model!r} not found in Ollama "
                f"(pull it with `ollama pull {self.model}`)"
            )
        return True, ""

    async def aclose(self) -> None:
        await self._client.aclose()
