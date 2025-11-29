# utils/progress.py
import os
import redis
import json

REDIS_URL = os.environ.get("REDIS_URL")
_redis = None
def get_redis():
    global _redis
    if _redis is None:
        # redis.from_url handles rediss:// as well
        _redis = redis.from_url(REDIS_URL, decode_responses=True)
    return _redis

def set_progress(job_id: str, status: str, progress: int, extra: dict=None):
    r = get_redis()
    key = f"job:{job_id}:status"
    payload = {"status": status, "progress": int(progress)}
    if extra:
        payload.update(extra)
    r.set(key, json.dumps(payload), ex=60*60*6)
    # also keep a separate short channel for live streaming if needed
    r.publish("jobs_channel", json.dumps({"job_id":job_id, **payload}))

def get_progress(job_id: str):
    r = get_redis()
    key = f"job:{job_id}:status"
    v = r.get(key)
    if v:
        return json.loads(v)
    return None
