# services/celery_app.py
"""
Celery app factory for Visora backend.

Usage:
    from services.celery_app import celery
    # or create a new celery with make_celery('name')

This file:
 - Reads REDIS_URL and optional CELERY variables from environment.
 - Detects TLS (rediss://) and sets broker_use_ssl / result_backend SSL options.
 - Autodiscovers tasks in common modules (adjust `included_modules` as needed).
 - Configures robust serializers, task time limits, concurrency-safe settings, logging.
"""

import os
import ssl
import logging
from urllib.parse import urlparse, urlunparse

from celery import Celery

LOG = logging.getLogger("visora.celery")
LOG.setLevel(logging.INFO)
if not LOG.handlers:
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s:%(name)s: %(message)s"))
    LOG.addHandler(ch)


def _normalize_redis_url(redis_url: str) -> str:
    """
    Normalize the redis URL so Celery can use it as broker/result_backend.
    Celery accepts 'redis://' or 'rediss://'. Upstash provides 'rediss://...'.
    """
    if not redis_url:
        return None
    parsed = urlparse(redis_url)
    # If URL contains query params (like ?ssl_cert_reqs=...), keep them
    return urlunparse(parsed)


def _ssl_config_from_url(redis_url: str):
    """
    Return broker_use_ssl dict and backend_use_ssl flag based on the url scheme.
    If scheme is 'rediss', return cert_reqs=CERT_NONE by default (safer to require CERT_REQUIRED in prod).
    """
    if not redis_url:
        return None

    parsed = urlparse(redis_url)
    scheme = parsed.scheme.lower()

    # default: no ssl settings
    broker_use_ssl = None

    if scheme in ("rediss", "rediss+srv"):
        # Use python ssl module constants. Upstash TLS sometimes requires disabling cert verify
        # if you want to skip verification: ssl.CERT_NONE
        # For production with proper CA, prefer ssl.CERT_REQUIRED
        ssl_cert_reqs_env = os.getenv("REDIS_SSL_CERT_REQS", "NONE").upper()  # allow override
        if ssl_cert_reqs_env == "REQUIRED":
            cert_req = ssl.CERT_REQUIRED
        elif ssl_cert_reqs_env == "OPTIONAL":
            cert_req = ssl.CERT_OPTIONAL
        else:
            cert_req = ssl.CERT_NONE

        broker_use_ssl = {
            "ssl_cert_reqs": cert_req,
        }

    return broker_use_ssl


def make_celery(app_name: str = "visora"):
    """
    Create and configure Celery instance.
    Environment variables used:
        REDIS_URL                  - full redis URL, e.g. rediss://default:...@host:6379
        CELERY_BROKER_POOL_LIMIT   - optional
        CELERY_TASK_SOFT_TIME_LIMIT - optional
        CELERY_TASK_TIME_LIMIT     - optional
        CELERY_CONCURRENCY         - optional (informational)
        REDIS_SSL_CERT_REQS        - override: REQUIRED | OPTIONAL | NONE (default NONE)
    """

    redis_url = os.getenv("REDIS_URL", os.getenv("UPSTASH_REDIS_URL", None))
    if not redis_url:
        LOG.warning("No REDIS_URL provided; Celery will start without broker (LOCAL TEST MODE).")

    broker_url = _normalize_redis_url(redis_url) if redis_url else None
    result_backend = broker_url  # use same redis for results (common pattern)

    broker_use_ssl = _ssl_config_from_url(redis_url)

    # Create Celery
    celery = Celery(app_name, broker=broker_url, backend=result_backend)

    # Basic recommended configuration for production
    celery.conf.update(
        accept_content=["json"],
        task_serializer="json",
        result_serializer="json",
        enable_utc=True,
        timezone="UTC",
        worker_max_tasks_per_child=100,  # avoid memory leaks
        worker_prefetch_multiplier=1,    # fair dispatch
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        broker_pool_limit=int(os.getenv("CELERY_BROKER_POOL_LIMIT", 10)),
        task_soft_time_limit=int(os.getenv("CELERY_TASK_SOFT_TIME_LIMIT", 300)),  # seconds
        task_time_limit=int(os.getenv("CELERY_TASK_TIME_LIMIT", 600)),  # seconds
        task_track_started=True,
        result_expires=int(os.getenv("CELERY_RESULT_EXPIRES", 60 * 60)),  # 1 hour
        worker_concurrency=int(os.getenv("CELERY_CONCURRENCY", 4)),
    )

    # Apply SSL settings for broker if needed
    if broker_use_ssl:
        # Celery expects broker_use_ssl (dict or list of dicts)
        celery.conf.broker_use_ssl = broker_use_ssl
        LOG.info("Configured broker_use_ssl: %s", broker_use_ssl)

    # If Redis backend requires TLS options, Celery does not have direct backend_use_ssl
    # but some versions accept result_backend_transport_options. For safety, try to set generic options.
    if broker_use_ssl:
        # Some transports use 'ssl' mapping, some use 'ssl_cert_reqs' directly. Provide both common options.
        celery.conf.result_backend_transport_options = {
            "ssl": {"cert_reqs": broker_use_ssl.get("ssl_cert_reqs")},
            # fallback direct flag
            "ssl_cert_reqs": broker_use_ssl.get("ssl_cert_reqs"),
        }

    # autodiscover tasks - adjust module list to your project structure
    included_modules = [
        "tasks.render_task",
        "tasks.rendr_task",
        "tasks.housekeeping",
        # add other task modules here or set autodiscover to package 'tasks'
    ]

    # Use autodiscover from a package name; if your tasks are in `tasks` package, autodiscover that.
    try:
        celery.autodiscover_tasks(["tasks", "services"], force=True)
    except Exception as ex:
        LOG.warning("autodiscover_tasks() raised: %s. Proceeding — you can register tasks manually.", ex)

    LOG.info("Celery app '%s' created. broker=%s", app_name, broker_url or "NONE")

    return celery


# create a default celery instance for imports
celery = make_celery("visora")

# Example: register a simple test task if needed (uncomment if you want quick smoke test)
# @celery.task(name="visora.ping")
# def ping():
#     return "pong"

# Optional helper: function to gracefully stop worker from code (if needed)
def shutdown_workers():
    """
    Request shutdown of workers via broadcast (if running).
    Useful in integration scripts / tests.
    """
    try:
        celery.control.broadcast("shutdown")
    except Exception as e:
        LOG.exception("Error sending shutdown broadcast: %s", e)
