"""Scheduled authenticated history backfill; asset acquisition stays in MemePump."""

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import schedule

logger = logging.getLogger(__name__)


class ImportState:
    """Track scheduled import runs."""

    def __init__(self, directory):
        self.path = Path(directory) / "meme_import_state.json"
        self.last_scan_time = None
        self.total_runs = 0

    @classmethod
    def load(cls, directory):
        state = cls(directory)
        if state.path.exists():
            try:
                data = json.loads(state.path.read_text(encoding="utf-8"))
                if data.get("last_scan_time"):
                    state.last_scan_time = datetime.fromisoformat(data["last_scan_time"])
                state.total_runs = data.get("total_runs", 0)
            except Exception as e:
                logger.warning(f"Failed to load import state: {e}")
        return state

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "last_scan_time": self.last_scan_time.isoformat() if self.last_scan_time else None,
            "total_runs": self.total_runs,
        }
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


async def run_scheduled_backfill(runtime, core, onebot, wake):
    """Execute backfill within its original capture freshness and authority bounds."""
    from .backfill import backfill

    state = ImportState.load(runtime.spool.directory)
    logger.info(f"Starting scheduled meme import via backfill (run #{state.total_runs + 1})")

    try:
        report = await backfill(runtime, core, onebot, wake)
        if report["status"] != "COMPLETE":
            logger.warning("Scheduled backfill unavailable; successful scan state unchanged")
            return
        added = report["added"]
        state.last_scan_time = datetime.now(UTC)
        state.total_runs += 1
        state.save()
        logger.info(f"Scheduled import completed: {added} messages backfilled")
    except Exception as e:
        logger.error(f"Scheduled import failed: {e}", exc_info=True)


def schedule_import_job(
    runtime,
    core,
    onebot,
    wake,
    enabled: bool = True,
    loop: asyncio.AbstractEventLoop = None,
):
    """Register daily backfill job at 03:30."""
    if not enabled:
        logger.info("Scheduled meme import is disabled")
        return

    def job():
        if loop is None:
            event_loop = asyncio.get_event_loop()
        else:
            event_loop = loop
        event_loop.create_task(run_scheduled_backfill(runtime, core, onebot, wake))

    schedule.every().day.at("03:30").do(job)
    logger.info("Scheduled meme import registered (daily at 03:30 via backfill)")


async def run_scheduler_loop(stop):
    """Background task for scheduled jobs."""
    from .app import pause

    while not stop.is_set():
        schedule.run_pending()
        # A stop must not wait out the minute: drain cancels loops after its grace.
        await pause(stop, 60)
