from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

EXTRACTION_SYSTEM_PROMPT = """You are extracting structured fields from a job posting.
Read the posting text and return JSON matching the given schema. Use null for any
field you cannot determine from the text — never guess. For remote, use exactly one
of "onsite", "hybrid", or "remote" based on what the posting states."""


class PostingExtraction(BaseModel):
    seniority: str | None = None
    min_years: int | None = None
    required_skills: list[str] = []
    nice_skills: list[str] = []
    degree_fields: list[str] = []
    location: str | None = None
    remote: Literal["onsite", "hybrid", "remote"] | None = None
    role_family: str | None = None
