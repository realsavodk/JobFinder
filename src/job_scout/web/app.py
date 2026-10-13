from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, SQLModel, create_engine, select

from job_scout.config import database_url
from job_scout.models import CompanyRunStatus, Posting
from job_scout.pipeline.run import RunResult, run_all
from job_scout.scheduler import start_scheduler

COMPANIES_FILE = Path("companies.yaml")
PROFILE_FILE = Path("profile.yaml")

_run_task: asyncio.Task[list[RunResult]] | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    scheduler = start_scheduler()
    yield
    scheduler.shutdown()


app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

_engine = create_engine(database_url())
SQLModel.metadata.create_all(_engine)


def _session() -> Session:
    return Session(_engine)


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    min_score: int = 0,
    company: str = "",
    remote_only: bool = False,
    new_only: bool = False,
) -> HTMLResponse:
    last_visit_cookie = request.cookies.get("last_visit")
    last_visit = (
        datetime.fromisoformat(last_visit_cookie) if last_visit_cookie else None
    )

    with _session() as session:
        query = select(Posting).where(
            Posting.status != "hidden", Posting.duplicate_of_id.is_(None)
        )
        if min_score:
            query = query.where(Posting.score >= min_score)
        if company:
            query = query.where(Posting.company == company)
        if remote_only:
            query = query.where(Posting.location.ilike("%remote%"))
        if new_only and last_visit is not None:
            query = query.where(Posting.first_seen > last_visit)
        query = query.order_by(Posting.score.desc(), Posting.posted_at.desc())
        postings = session.exec(query).all()

        companies = sorted(session.exec(select(Posting.company).distinct()).all())
        run_statuses = session.exec(select(CompanyRunStatus)).all()

    response = templates.TemplateResponse(
        request,
        "index.html",
        {
            "postings": postings,
            "reasons_by_id": {p.id: p.score_reasons for p in postings},
            "companies": companies,
            "run_statuses": run_statuses,
            "filters": {
                "min_score": min_score,
                "company": company,
                "remote_only": remote_only,
                "new_only": new_only,
            },
        },
    )
    response.set_cookie("last_visit", datetime.now(UTC).isoformat())
    return response


@app.post("/postings/{posting_id}", response_class=HTMLResponse)
def update_posting(
    request: Request,
    posting_id: int,
    status: str = Form(...),
    notes: str = Form(""),
    follow_up_date: str = Form(""),
) -> HTMLResponse:
    with _session() as session:
        posting = session.get(Posting, posting_id)
        if posting is None:
            return HTMLResponse(status_code=404)
        posting.status = status
        posting.notes = notes or None
        posting.follow_up_date = (
            date.fromisoformat(follow_up_date) if follow_up_date else None
        )
        session.add(posting)
        session.commit()
        session.refresh(posting)
        reasons = posting.score_reasons

    return templates.TemplateResponse(
        request, "_posting_row.html", {"posting": posting, "reasons": reasons}
    )


@app.post("/run")
async def run_now() -> RedirectResponse:
    global _run_task
    # A single run can now take a while (sequential LLM extraction), so don't
    # block the request — and don't let a double-click race two runs against
    # the same SQLite file.
    if _run_task is None or _run_task.done():
        _run_task = asyncio.create_task(run_all(COMPANIES_FILE, PROFILE_FILE))
    return RedirectResponse(url="/", status_code=303)


@app.get("/config", response_class=HTMLResponse)
def config(request: Request) -> HTMLResponse:
    companies_text = (
        COMPANIES_FILE.read_text() if COMPANIES_FILE.exists() else "(missing)"
    )
    profile_text = PROFILE_FILE.read_text() if PROFILE_FILE.exists() else "(missing)"
    return templates.TemplateResponse(
        request,
        "config.html",
        {"companies_text": companies_text, "profile_text": profile_text},
    )
