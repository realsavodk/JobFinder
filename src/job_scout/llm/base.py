from __future__ import annotations

from abc import ABC, abstractmethod

from job_scout.llm.extraction import PostingExtraction


class ExtractionError(Exception):
    pass


class LLMProvider(ABC):
    @abstractmethod
    async def extract(self, posting_text: str) -> PostingExtraction: ...

    @abstractmethod
    async def check_available(self) -> tuple[bool, str]: ...

    @abstractmethod
    async def aclose(self) -> None: ...
