from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel


class SkillWeight(BaseModel):
    name: str
    weight: float = 1.0


class Profile(BaseModel):
    majors: list[str] = []
    skills: list[SkillWeight] = []
    tools: list[str] = []
    years_experience: float = 0
    seniority_targets: list[str] = []
    locations: list[str] = []
    remote_ok: bool = False
    exclude_keywords: list[str] = []


def load_profile(path: Path) -> Profile:
    data = yaml.safe_load(path.read_text()) or {}
    return Profile(**data)
