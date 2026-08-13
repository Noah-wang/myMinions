from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler


_scheduler: AsyncIOScheduler | None = None


def start_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
    if not _scheduler.running:
        _scheduler.start()
    return _scheduler


def add_interval_job(
    job_id: str,
    callback: Callable[..., Awaitable[Any]],
    minutes: int,
    args: list[Any] | None = None,
    run_immediately: bool = True,
) -> None:
    scheduler = start_scheduler()
    scheduler.add_job(
        callback,
        "interval",
        minutes=max(minutes, 1),
        args=args or [],
        id=job_id,
        replace_existing=True,
        next_run_time=datetime.now() if run_immediately else None,
    )
