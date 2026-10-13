from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel, UniqueConstraint


class Posting(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("source", "company", "external_id"),)

    id: int | None = Field(default=None, primary_key=True)
    source: str = Field(index=True)
    company: str = Field(index=True)
    external_id: str = Field(index=True)
    title: str
    location: str
    url: str
    description_html: str
    posted_at: datetime | None = None
    updated_at: datetime | None = None
    first_seen: datetime
    last_seen: datetime

    # new/interested/applied/rejected/hidden are user-set; closed is set by the expire step
    status: str = Field(default="new", index=True)
    notes: str | None = None
    follow_up_date: date | None = None

    score: int | None = Field(default=None, index=True)
    score_reasons: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    # "pending" (not yet attempted) | "failed" (attempted, gave up after retry) | "success"
    extraction_status: str = Field(default="pending", index=True)
    extraction_hash: str | None = (
        None  # sha256 of stripped (pre-truncation) description text
    )
    seniority: str | None = None
    min_years: int | None = None
    required_skills: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    nice_skills: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    degree_fields: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    extracted_location: str | None = None
    remote: str | None = None  # "onsite" | "hybrid" | "remote"
    role_family: str | None = None

    # cross-source dedup: points at the canonical posting when this one is a duplicate
    duplicate_of_id: int | None = Field(default=None, foreign_key="posting.id")


class CompanyRunStatus(SQLModel, table=True):
    company: str = Field(primary_key=True)
    last_run_at: datetime | None = None
    ok: bool = True
    message: str = ""
    postings_count: int = 0
    closed_count: int = 0
