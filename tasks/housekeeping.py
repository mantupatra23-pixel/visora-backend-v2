# tasks/housekeeping.py
import os
from pathlib import Path
from datetime import datetime, timedelta
from services.celery_app import celery_app

JOBS_DIR = Path(os.environ.get("JOBS_DIR", "jobs"))
VIDEO_DIR = Path(os.environ.get("VIDEO_SAVE_DIR", "public/videos"))

@celery_app.task(bind=True, name="tasks.housekeeping.cleanup_old_jobs")
def cleanup_old_jobs(self):
    now = datetime.utcnow()
    # remove failed jobs older than 24 hours
    for p in JOBS_DIR.glob("*.json"):
        try:
            import json
            d = json.loads(p.read_text())
            status = d.get("status")
            created_at = d.get("created_at")
            if created_at:
                ts = datetime.fromisoformat(created_at)
                if status == "failed" and (now - ts) > timedelta(hours=24):
                    p.unlink(missing_ok=True)
            # also remove completed jobs JSON older than X days
            if created_at:
                ts = datetime.fromisoformat(created_at)
                if (now - ts) > timedelta(days=int(os.environ.get("FILE_RETENTION_DAYS", 7))):
                    p.unlink(missing_ok=True)
        except Exception:
            continue

    # cleanup old video files
    retention_days = int(os.environ.get("FILE_RETENTION_DAYS", 7))
    for v in VIDEO_DIR.glob("*.mp4"):
        try:
            mtime = datetime.utcfromtimestamp(v.stat().st_mtime)
            if (now - mtime).days > retention_days:
                v.unlink(missing_ok=True)
        except Exception:
            continue
