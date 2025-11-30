# services/celery_app.py
import os
import logging
from urllib.parse import urlparse, parse_qs

from celery import Celery, Task

logger = logging.getLogger("visora.celery")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

# Read broker/result backend from env (set REDIS_URL in env)
REDIS_URL = os.environ.get("REDIS_URL", os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0"))

def _make_celery(broker_url: str, backend_url: str = None):
    """
    Create a Celery app instance with safe defaults.
    Handles 'rediss://' (ssl) by setting broker_use_ssl when needed.
    """
    broker_opts = {}
    broker_use_ssl = None

    parsed = urlparse(broker_url)
    if parsed.scheme.startswith("rediss"):
        # Many hosted redis services don't provide certs - default to not verifying
        # If you want strict verification, set ssl_cert_reqs via query param or change here.
        broker_use_ssl = {"ssl_cert_reqs": False}

        # If user passed ssl_cert_reqs in query string, respect it:
        qs = parse_qs(parsed.query)
        v = qs.get("ssl_cert_reqs") or qs.get("ssl_cert_req")
        if v:
            # if user supplied CERT_REQUIRED/CERT_NONE etc -> map to bool
            val = v[0].upper()
            if val in ("CERT_NONE", "NONE", "0", "FALSE"):
                broker_use_ssl = {"ssl_cert_reqs": False}
            else:
                # default to verify if they explicitly requested
                broker_use_ssl = {"ssl_cert_reqs": True}

    if broker_use_ssl:
        broker_opts["broker_use_ssl"] = broker_use_ssl

    celery = Celery(
        "visora",
        broker=broker_url,
        backend=backend_url or broker_url,
        include=[],  # tasks will be autodiscovered
    )

    # Basic recommended conf
    celery.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        result_expires=3600,
        timezone="UTC",
        enable_utc=True,
        broker_transport_options={"visibility_timeout": 3600},
        worker_prefetch_multiplier=1,
        task_acks_late=True,
        task_routes={
            # default mapping: tasks.render_task.* -> queue 'renderers'
            "tasks.render_task.*": {"queue": "renderers"},
        },
        worker_concurrency=int(os.environ.get("CELERY_CONCURRENCY", "1")),
        **broker_opts,
    )

    # autodiscover tasks in your project 'tasks' package
    try:
        celery.autodiscover_tasks(["tasks"])
    except Exception:
        logger.warning("autodiscover_tasks failed; ensure tasks package exists")

    return celery

# single module-level celery app
celery = _make_celery(REDIS_URL, REDIS_URL)

# Optional: integrate with Flask app context (call init_app(app) from your Flask factory)
_flask_app = None

class ContextTask(Task):
    """Make Celery tasks run inside Flask app context if init_app was called."""
    _app = None
    abstract = True

    def __call__(self, *args, **kwargs):
        if ContextTask._app:
            with ContextTask._app.app_context():
                return self.run(*args, **kwargs)
        return self.run(*args, **kwargs)

def init_app(app):
    """
    Call this from your Flask app (after creating Flask app) to integrate.
    Example:
        from services.celery_app import init_app, celery
        init_app(app)
        celery.start()  # or run workers separately
    """
    global _flask_app
    _flask_app = app
    ContextTask._app = app
    celery.Task = ContextTask
    logger.info("Celery integrated with Flask app context")

# helper: simple decorator to register tasks easily (optional)
def task(*args, **kwargs):
    return celery.task(*args, **kwargs)

# Example simple health-check task (you can remove)
@celery.task(name="visora.ping")
def ping():
    logger.info("ping task executed")
    return {"ok": True}

# Optionally expose a convenience start for local dev (do not call in production gunicorn worker)
def start_worker(argv=None):
    """
    Start a celery worker programmatically (useful for local debug).
    Production: run `celery -A services.celery_app.celery worker ...` instead.
    """
    argv = argv or []
    from celery.bin import worker as celery_worker
    worker = celery_worker.worker(app=celery)
    worker.run(argv=argv)
