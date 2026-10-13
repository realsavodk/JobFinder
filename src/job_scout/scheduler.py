from __future__ import annotations

from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from job_scout.pipeline.run import run_all

COMPANIES_FILE = Path("companies.yaml")
PROFILE_FILE = Path("profile.yaml")
DAILY_FETCH_JOB_ID = "daily_fetch"


def start_scheduler(*, hour: int = 6, minute: int = 0) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_all,
        "cron",
        hour=hour,
        minute=minute,
        args=[COMPANIES_FILE, PROFILE_FILE],
        id=DAILY_FETCH_JOB_ID,
    )
    scheduler.start()
    return scheduler
