import os
from celery import Celery

REDIS_URL = os.environ.get("REDIS_URL")

app = Celery(
    "visora",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

app.conf.update(
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": 3600},
    result_backend_transport_options={"visibility_timeout": 3600},
)
