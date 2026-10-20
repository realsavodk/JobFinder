from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, field_validator


class RawPosting(BaseModel):
    source: str
    company: str
    external_id: str
    title: str
    location: str
    url: str
    # None means "not refetched this run, keep whatever is already stored" (see pipeline.upsert)
    description_html: str | None
    posted_at: datetime | None
    updated_at: datetime | None

    @field_validator("location", mode="before")
    @classmethod
    def _missing_location_is_blank(cls, value: str | None) -> str:
        # Some boards send `"location": null`; that must not fail the whole company.
        return value or ""
