"""Background scheduler for automated project scanning.

Runs a background thread that executes incremental scans at a configurable time
(default 07:00 daily). Uses APScheduler for reliable cron-style scheduling.
"""
import logging
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from code_hub.scanner import scan_changed_projects

logger = logging.getLogger(__name__)

_scheduler: Optional[BackgroundScheduler] = None


def scheduled_scan():
    """Execute scheduled incremental scan."""
    logger.info(f"Starting scheduled scan at {datetime.now().isoformat()}")
    try:
        result = scan_changed_projects(triggered_by="scheduled")
        logger.info(
            f"Scheduled scan complete: {result['projects_scanned']} projects scanned, "
            f"{len(result['errors'])} errors"
        )
        if result['errors']:
            for error in result['errors']:
                logger.error(f"Scan error: {error}")
    except Exception as e:
        logger.exception(f"Scheduled scan failed: {e}")


def start_scheduler(hour: int = 7, minute: int = 0) -> BackgroundScheduler:
    """Start the background scheduler for daily scans.

    Args:
        hour: Hour to run scan (0-23, default 7 for 07:00)
        minute: Minute to run scan (0-59, default 0)

    Returns:
        The running scheduler instance
    """
    global _scheduler

    if _scheduler is not None and _scheduler.running:
        logger.warning("Scheduler already running")
        return _scheduler

    _scheduler = BackgroundScheduler()

    # Add daily scan job at specified time
    _scheduler.add_job(
        scheduled_scan,
        trigger=CronTrigger(hour=hour, minute=minute),
        id='daily_scan',
        name='Daily incremental project scan',
        replace_existing=True
    )

    _scheduler.start()
    logger.info(f"Scheduler started. Daily scan scheduled for {hour:02d}:{minute:02d}")

    return _scheduler


def stop_scheduler():
    """Stop the background scheduler."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Scheduler stopped")


def get_scheduler() -> Optional[BackgroundScheduler]:
    """Get the current scheduler instance."""
    return _scheduler


def get_next_scan_time() -> Optional[datetime]:
    """Get the next scheduled scan time."""
    if _scheduler is None:
        return None

    job = _scheduler.get_job('daily_scan')
    if job is None:
        return None

    return job.next_run_time


def trigger_scan_now() -> dict:
    """Manually trigger an immediate scan."""
    return scan_changed_projects(triggered_by="manual")
