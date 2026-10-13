import asyncio

from job_scout.scheduler import DAILY_FETCH_JOB_ID, start_scheduler


def test_start_scheduler_registers_daily_job():
    # AsyncIOScheduler.start() requires a running event loop, same as when
    # it's started from the FastAPI app's lifespan under uvicorn.
    async def run() -> None:
        scheduler = start_scheduler(hour=6, minute=0)
        try:
            job = scheduler.get_job(DAILY_FETCH_JOB_ID)
            assert job is not None
            hour_field = next(f for f in job.trigger.fields if f.name == "hour")
            assert hour_field.expressions[0].first == 6
        finally:
            scheduler.shutdown()

    asyncio.run(run())
