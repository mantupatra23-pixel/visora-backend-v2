# worker.py
"""
Run this as worker process (Render background service or docker / systemd).
"""
import os
from redis import Redis
from rq import Worker, Queue, Connection

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
listen = ["default"]
redis_conn = Redis.from_url(REDIS_URL)

if __name__ == "__main__":
    with Connection(redis_conn):
        worker = Worker(list(map(Queue, listen)))
        worker.work()
