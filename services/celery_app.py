# services/celery_app.py
import os
from celery import Celery

REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")

celery_app = Celery(
    "visora",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

# Recommended Celery config (tune as needed)
celery_app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    result_expires=3600,
    task_track_started=True,
)

# Optional: periodic cleanup schedule example
celery_app.conf.beat_schedule = {
    "cleanup-old-jobs-every-hour": {
        "task": "tasks.housekeeping.cleanup_old_jobs",
        "schedule": 3600.0,
        "args": ()
    }
}
