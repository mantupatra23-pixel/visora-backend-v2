# services/celery_app.py
"""
Celery app initialization and helper to enqueue render jobs.
"""
import os
import logging
from celery import Celery
from kombu import Exchange, Queue

logger = logging.getLogger("visora_celery")
logging.basicConfig(level=logging.INFO)

REDIS_URL = os.environ.get("REDIS_URL") or os.environ.get("CELERY_BROKER_URL") or os.environ.get("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery("visora_tasks", broker=REDIS_URL, backend=REDIS_URL)

# optional: configuration
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    worker_prefetch_multiplier=1,
    task_acks_late=True,
)

# define default queue
celery_app.conf.task_default_queue = "celery"
celery_app.conf.task_queues = (
    Queue("celery", Exchange("celery"), routing_key="celery"),
)

# import tasks to register them (relative import)
try:
    from tasks import render_task  # noqa: F401
    logger.info("Imported tasks.render_task")
except Exception:
    logger.exception("Failed importing tasks.render_task")

# helper to enqueue job by id
def enqueue_render_job(job_id: str):
    if not job_id:
        raise ValueError("job_id required")
    # call Celery task
    result = celery_app.send_task("tasks.render_task.render_job_task", args=[job_id], queue="celery")
    logger.info("Enqueued job %s -> %s", job_id, result.id)
    return result.id
