from __future__ import annotations

import asyncio
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, SQLModel, create_engine, func, select

from job_scout.config import database_url, load_companies
from job_scout.models import CompanyRunStatus, Posting
from job_scout.pipeline import run as pipeline_run
from job_scout.pipeline.run import RunResult, run_all
from job_scout.regions import CA_PROVINCES, US_STATES, regions_in
from job_scout.resume_import import ResumeImportError, import_resume
from job_scout.scheduler import start_scheduler

COMPANIES_FILE = Path("companies.yaml")
PROFILE_FILE = Path("profile.yaml")
RESUME_SUFFIXES = {".pdf", ".txt", ".md"}
MAX_RESUME_BYTES = 5 * 1024 * 1024
# Discovered companies bring tens of thousands of postings; rendering them all made
# a 50 MB page. Score 0 means excluded or no skill match, so it's hidden by default.
MAX_ROWS = 500
FOLLOW_UP_AFTER = timedelta(days=7)
# Jobs page tabs: label and the posting statuses each one shows.
TABS = {
    "todo": ("To apply", ("new", "interested")),
    "applied": ("Applied", ("applied",)),
    "archive": ("Archive", ("rejected", "hidden", "closed")),
}

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
    min_score: int = 1,
    company: str = "",
    remote_only: bool = False,
    new_only: bool = False,
    region: str = "",
    tab: str = "todo",
) -> HTMLResponse:
    tab = tab if tab in TABS else "todo"
    last_visit_cookie = request.cookies.get("last_visit")
    last_visit = (
        datetime.fromisoformat(last_visit_cookie) if last_visit_cookie else None
    )

    with _session() as session:
        status_counts = dict(
            session.exec(
                select(Posting.status, func.count())
                .where(Posting.duplicate_of_id.is_(None))
                .group_by(Posting.status)
            ).all()
        )
        tab_counts = {
            key: sum(status_counts.get(s, 0) for s in statuses)
            for key, (_label, statuses) in TABS.items()
        }
        query = select(Posting).where(
            Posting.status.in_(TABS[tab][1]), Posting.duplicate_of_id.is_(None)
        )
        if tab == "applied":
            # Next steps first: soonest follow-up at the top, undated ones last.
            query = query.order_by(
                Posting.follow_up_date.is_(None), Posting.follow_up_date
            )
        elif tab == "archive":
            query = query.order_by(Posting.last_seen.desc())
        # Filters narrow the big to-apply list; Applied and Archive always show all.
        if tab == "todo":
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
        # Free-text locations can't be matched in SQL, so regions are filtered here.
        posting_regions = {p.id: regions_in(p.location) for p in postings}
        region_counts: dict[str, int] = {}
        for codes in posting_regions.values():
            for code in codes:
                region_counts[code] = region_counts.get(code, 0) + 1
        if region and tab == "todo":
            postings = [p for p in postings if region in posting_regions[p.id]]
        total_matches = len(postings)
        postings = postings[:MAX_ROWS]

        companies = sorted(session.exec(select(Posting.company).distinct()).all())

    response = templates.TemplateResponse(
        request,
        "index.html",
        {
            "postings": postings,
            "total_matches": total_matches,
            "tab": tab,
            "tabs": TABS,
            "tab_counts": tab_counts,
            "today": date.today(),
            "reasons_by_id": {p.id: p.score_reasons for p in postings},
            "companies": companies,
            "region_groups": [("United States", US_STATES), ("Canada", CA_PROVINCES)],
            "region_counts": region_counts,
            "filters": {
                "min_score": min_score,
                "company": company,
                "remote_only": remote_only,
                "new_only": new_only,
                "region": region,
            },
        },
    )
    if tab == "todo":  # browsing other tabs shouldn't reset "new since last visit"
        response.set_cookie("last_visit", datetime.now(UTC).isoformat())
    return response


@app.post("/postings/{posting_id}", response_class=HTMLResponse)
def update_posting(
    request: Request,
    posting_id: int,
    status: str = Form(...),
    notes: str = Form(""),
    follow_up_date: str = Form(""),
    tab: str = Form(""),
) -> HTMLResponse:
    with _session() as session:
        posting = session.get(Posting, posting_id)
        if posting is None:
            return HTMLResponse(status_code=404)
        just_applied = status == "applied" and posting.status != "applied"
        posting.status = status
        posting.notes = notes or None
        posting.follow_up_date = (
            date.fromisoformat(follow_up_date) if follow_up_date else None
        )
        if just_applied and posting.follow_up_date is None:
            posting.follow_up_date = date.today() + FOLLOW_UP_AFTER
        session.add(posting)
        session.commit()
        session.refresh(posting)
        reasons = posting.score_reasons

    if tab in TABS and posting.status not in TABS[tab][1]:
        return HTMLResponse("")  # it moved to another tab: drop the row from this one
    return templates.TemplateResponse(
        request,
        "_posting_row.html",
        {"posting": posting, "reasons": reasons, "tab": tab, "today": date.today()},
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


def _render_config(
    request: Request, resume_lines: list[str] | None = None, resume_error: str = ""
) -> HTMLResponse:
    companies_text = (
        COMPANIES_FILE.read_text() if COMPANIES_FILE.exists() else "(missing)"
    )
    profile_text = PROFILE_FILE.read_text() if PROFILE_FILE.exists() else "(missing)"
    return templates.TemplateResponse(
        request,
        "config.html",
        {
            "companies_text": companies_text,
            "profile_text": profile_text,
            "resume_lines": resume_lines,
            "resume_error": resume_error,
        },
        status_code=400 if resume_error else 200,
    )


@app.get("/companies", response_class=HTMLResponse)
def companies_page(request: Request) -> HTMLResponse:
    with _session() as session:
        statuses = session.exec(select(CompanyRunStatus)).all()
    # Companies removed from companies.yaml keep their old status rows; hide those.
    if COMPANIES_FILE.exists():
        current = {c.name for c in load_companies(COMPANIES_FILE)}
        statuses = [s for s in statuses if s.company in current]
    # Failures first: they're the rows that need attention.
    statuses = sorted(statuses, key=lambda s: (s.ok, s.company.lower()))
    return templates.TemplateResponse(
        request,
        "companies.html",
        {"statuses": statuses, "failed": sum(not s.ok for s in statuses)},
    )


@app.get("/progress", response_class=HTMLResponse)
def run_progress(request: Request, polling: bool = False) -> HTMLResponse:
    # polling=True means the bar was already showing a run, so "not running" now
    # means it just finished; a fresh page load with no run shows nothing.
    return templates.TemplateResponse(
        request,
        "_progress.html",
        {"progress": pipeline_run.progress, "polling": polling},
    )


@app.get("/config", response_class=HTMLResponse)
def config(request: Request) -> HTMLResponse:
    return _render_config(request)


@app.post("/resume", response_class=HTMLResponse)
async def upload_resume(
    request: Request, resume: Annotated[UploadFile, File()]
) -> HTMLResponse:
    suffix = Path(resume.filename or "").suffix.lower()
    if suffix not in RESUME_SUFFIXES:
        return _render_config(request, resume_error="Upload a .pdf, .txt or .md file.")
    content = await resume.read(MAX_RESUME_BYTES + 1)
    if len(content) > MAX_RESUME_BYTES:
        return _render_config(request, resume_error="Resume is larger than 5 MB.")

    with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
        tmp.write(content)
        tmp.flush()
        try:
            lines = await import_resume(Path(tmp.name), PROFILE_FILE)
        except (ResumeImportError, UnicodeDecodeError) as exc:
            return _render_config(
                request, resume_error=f"Couldn't import resume: {exc}"
            )
    return _render_config(request, resume_lines=lines)
