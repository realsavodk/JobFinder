from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()


class CompanyConfig(BaseModel):
    name: str
    ats: str
    token: str | None = None
    tenant: str | None = None
    host: str | None = None
    site: str | None = None
    searchText: str = ""
    # Workday list filter, e.g. {"State__Region__Province": ["<id>"]}; set by discover.
    appliedFacets: dict[str, list[str]] = {}


def load_companies(path: Path) -> list[CompanyConfig]:
    entries = yaml.safe_load(path.read_text()) or []
    return [CompanyConfig(**entry) for entry in entries]


def database_url() -> str:
    return os.environ.get("DATABASE_URL", "sqlite:///job_scout.db")


class LLMConfig(BaseModel):
    provider: str = "ollama"
    model: str = ""
    num_ctx: int = 8192
    ollama_base_url: str = "http://localhost:11434"
    api_key: str = ""
    base_url: str = ""


def load_llm_config() -> LLMConfig:
    return LLMConfig(
        provider=os.environ.get("LLM_PROVIDER", "ollama"),
        model=os.environ.get("LLM_MODEL", ""),
        num_ctx=int(os.environ.get("LLM_NUM_CTX", "8192")),
        ollama_base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        api_key=os.environ.get("LLM_API_KEY", ""),
        base_url=os.environ.get("LLM_BASE_URL", ""),
    )
