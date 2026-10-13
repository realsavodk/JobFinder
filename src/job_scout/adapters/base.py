from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


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
