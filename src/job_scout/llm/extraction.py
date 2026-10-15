from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

EXTRACTION_SYSTEM_PROMPT = """You are extracting structured fields from a job posting.
The text starts with "Title:" and "Location:" lines, then the description. Return
JSON matching the given schema. Use null (or [] for lists) for anything the text
does not state — never guess.

- seniority: one of "intern", "entry", "associate", "mid", "senior", "staff",
  "principal", "manager", "director", "executive" — no other values ("Lead" in
  a title means "senior"; "Junior", "New Grad", "Graduate" or "Early Career"
  means "entry"; "Intern", "Internship" or "Co-op" means "intern"). Take it from
  the title or an explicit level; years of experience alone don't set it.
- min_years: the smallest number of years of experience required, as an integer.
- required_skills / nice_skills: short names of concrete tools, languages,
  technologies or domains, 1-3 words each (e.g. "Python", "SQL", "Kubernetes",
  "threat intelligence"). Never include soft skills, traits or ways of working
  (communication, collaboration, ownership, empathy, mindset, leadership,
  problem-solving) — if it isn't something you could list as a tool or
  technical domain on a resume, leave it out. required = minimum/basic
  qualifications; nice = preferred/bonus.
- degree_fields: fields of study named for a degree, e.g. "Computer Science".
  Omit vague entries like "related field".
- location: copy the Location line as written.
- remote: "remote" only if the Location line or text explicitly says remote;
  "hybrid" if it requires some in-office days; otherwise "onsite". A list of
  office cities is "onsite", not remote. Never infer remote from the company or
  role type.
- role_family: one of "engineering", "data", "security", "trust_and_safety",
  "product", "design", "hardware", "sales", "business_development", "marketing",
  "operations", "finance", "legal", "people", "support", "other"."""


class PostingExtraction(BaseModel):
    seniority: str | None = None
    min_years: int | None = None
    required_skills: list[str] = []
    nice_skills: list[str] = []
    degree_fields: list[str] = []
    location: str | None = None
    remote: Literal["onsite", "hybrid", "remote"] | None = None
    role_family: str | None = None
