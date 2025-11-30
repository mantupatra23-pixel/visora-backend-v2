from celery import Celery
import os

# ==========================
# Redis URL from environment
# ==========================

REDIS_URL = os.getenv("REDIS_URL")

if not REDIS_URL:
    raise Exception("REDIS_URL environment variable not set!")

# ==========================
# Celery App
# ==========================

celery_app = Celery(
    "visora",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

celery_app.conf.update(
    broker_connection_retry_on_startup=True,
    broker_transport_options={
        "ssl": {
            "cert_reqs": "CERT_NONE"
        }
    },
    redis_backend_use_ssl={
        "cert_reqs": "CERT_NONE"
    },
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Kolkata",
    enable_utc=True,
)

# ==========================
# Default task (for testing)
# ==========================

@celery_app.task
def test_task(text):
    return f"Task completed: {text}"
