from __future__ import annotations

from pydantic import BaseModel

RESUME_SYSTEM_PROMPT = """You are extracting structured fields from a resume.
Return JSON matching the given schema. Never guess — only use what the resume states.

- skills: short names of concrete tools, languages, frameworks, technologies or
  technical domains, 1-3 words each (e.g. "Python", "SQL", "React", "Docker",
  "machine learning"). Include ones used in listed jobs and projects, not only a
  skills section. Never include soft skills or traits (communication,
  leadership, teamwork, problem-solving).
- roles: every job in the work experience section, one entry per position.
  - start / end: "YYYY-MM" (or "YYYY" if no month is given). end is null if the
    role is current ("Present", "Current", "Now").
  - internship: true for internships, co-ops and student/research assistant
    positions; false for regular jobs.
  Do not include education, projects, volunteering or clubs as roles."""


class Role(BaseModel):
    # No defaults: every field must be required in the schema Ollama's grammar sees.
    title: str
    start: str
    end: str | None
    internship: bool


class ResumeExtraction(BaseModel):
    skills: list[str]
    roles: list[Role]
