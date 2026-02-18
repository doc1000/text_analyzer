# app/scheduler.py
"""
Background scheduler for periodic tasks like deduplication.

Uses APScheduler to run tasks at specific times without blocking the main application.
"""
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

from .db import SessionLocal
from .topics import deduplicate_vault_documents
from .ingest_worker import process_pending_ingest_queue
from . import models

logger = logging.getLogger(__name__)

# Global scheduler instance
_scheduler: Optional[BackgroundScheduler] = None
_executor: Optional[ThreadPoolExecutor] = None


def run_scheduled_deduplication():
    """
    Run deduplication across all vaults.
    
    This function is called by the scheduler at midnight and noon.
    It runs in a background thread to avoid blocking the main application.
    """
    logger.info("=" * 60)
    logger.info(f"Starting scheduled deduplication at {datetime.utcnow().isoformat()}")
    logger.info("=" * 60)
    
    db: Session = SessionLocal()
    try:
        # Get all vault IDs
        vault_ids = db.query(models.Vault.id).all()
        
        if not vault_ids:
            logger.info("No vaults found, skipping deduplication")
            return
        
        total_url_removed = 0
        total_semantic_removed = 0
        vaults_processed = 0
        
        for (vault_id,) in vault_ids:
            try:
                logger.info(f"Processing vault {vault_id}...")
                result = deduplicate_vault_documents(db, vault_id)
                
                url_removed = result.get("url_duplicates_removed", 0)
                semantic_removed = result.get("semantic_duplicates_removed", 0)
                
                total_url_removed += url_removed
                total_semantic_removed += semantic_removed
                vaults_processed += 1
                
                logger.info(
                    f"  Vault {vault_id}: {url_removed} URL dupes, "
                    f"{semantic_removed} semantic dupes removed"
                )
            except Exception as e:
                logger.error(f"Error processing vault {vault_id}: {e}", exc_info=True)
                # Continue with next vault even if one fails
        
        logger.info("=" * 60)
        logger.info(f"Scheduled deduplication complete at {datetime.utcnow().isoformat()}")
        logger.info(f"Vaults processed: {vaults_processed}")
        logger.info(f"Total URL duplicates removed: {total_url_removed}")
        logger.info(f"Total semantic duplicates removed: {total_semantic_removed}")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"Fatal error in scheduled deduplication: {e}", exc_info=True)
    finally:
        db.close()


def run_pending_ingest_queue():
    """Process pending items in the ingest queue. Catches failed/stale items."""
    try:
        stats = process_pending_ingest_queue()
        if stats.get("processed") or stats.get("reset_stuck"):
            logger.info(f"Ingest queue: processed={stats.get('processed')}, reset_stuck={stats.get('reset_stuck')}")
    except Exception as e:
        logger.error(f"Error in ingest queue processing: {e}", exc_info=True)


def init_scheduler(schedule_times: list[str] = None):
    """
    Initialize and start the background scheduler.
    
    Always adds the ingest queue job (every 2 min). Adds deduplication jobs only when enabled.
    
    Args:
        schedule_times: List of time strings in HH:MM format (e.g., ["00:00", "12:00"])
                       If None, defaults to midnight and noon. Used for deduplication only.
    """
    global _scheduler, _executor
    
    if _scheduler is not None:
        logger.warning("Scheduler already initialized, skipping")
        return
    
    if schedule_times is None:
        schedule_times = ["00:00", "12:00"]
    
    logger.info("Initializing background scheduler")
    
    # Create thread pool executor for running blocking DB operations
    _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scheduler")
    
    # Create scheduler
    _scheduler = BackgroundScheduler(
        timezone="UTC",
        job_defaults={
            "coalesce": True,  # Combine multiple missed runs into one
            "max_instances": 1,  # Only one instance of each job at a time
        }
    )
    
    # Always add ingest queue job (every 2 minutes)
    _scheduler.add_job(
        func=run_pending_ingest_queue,
        trigger=IntervalTrigger(minutes=2),
        id="ingest_queue",
        name="Process ingest queue",
        replace_existing=True,
    )
    logger.info("Scheduled ingest queue processing every 2 minutes")

    # Schedule deduplication jobs (only when enabled)
    from .config import PREFERENCES
    if PREFERENCES.deduplication.enabled:
        for time_str in schedule_times:
            try:
                hour, minute = map(int, time_str.split(":"))
                trigger = CronTrigger(hour=hour, minute=minute, timezone="UTC")
                
                _scheduler.add_job(
                    func=run_scheduled_deduplication,
                    trigger=trigger,
                    id=f"dedupe_{time_str.replace(':', '')}",
                    name=f"Deduplication at {time_str} UTC",
                    replace_existing=True,
                )
                logger.info(f"Scheduled deduplication for {time_str} UTC (cron: {hour}:{minute:02d})")
            except ValueError as e:
                logger.error(f"Invalid time format '{time_str}': {e}")
    
    # Start the scheduler
    _scheduler.start()
    logger.info("Background scheduler started")


def shutdown_scheduler():
    """
    Gracefully shutdown the scheduler and wait for running jobs to complete.
    """
    global _scheduler, _executor
    
    if _scheduler is not None:
        logger.info("Shutting down background scheduler...")
        _scheduler.shutdown(wait=True)
        _scheduler = None
        logger.info("Background scheduler stopped")
    
    if _executor is not None:
        logger.info("Shutting down thread pool executor...")
        _executor.shutdown(wait=True)
        _executor = None
        logger.info("Thread pool executor stopped")


def get_scheduler_status() -> dict:
    """
    Get the current status of the scheduler and its jobs.
    
    Returns:
        Dictionary with scheduler status and job information.
    """
    global _scheduler
    
    if _scheduler is None:
        return {
            "running": False,
            "jobs": []
        }
    
    jobs = []
    for job in _scheduler.get_jobs():
        next_run = job.next_run_time.isoformat() if job.next_run_time else None
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": next_run,
        })
    
    return {
        "running": _scheduler.running,
        "jobs": jobs
    }
