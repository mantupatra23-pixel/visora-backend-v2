# utils/job_store.py
import json
from pathlib import Path
from typing import Dict, Any
import threading
import os
from datetime import datetime

JOBS_DIR = Path(os.environ.get("JOBS_DIR", "jobs"))
JOBS_DIR.mkdir(parents=True, exist_ok=True)
_lock = threading.Lock()

def job_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"

def read_job(job_id: str) -> Dict[str, Any]:
    p = job_path(job_id)
    if not p.exists():
        raise FileNotFoundError("job not found")
    return json.loads(p.read_text(encoding="utf-8"))

def write_job(job_id: str, payload: Dict[str, Any]):
    p = job_path(job_id)
    with _lock:
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

def create_job(job_id: str, payload: Dict[str, Any]):
    payload.setdefault("id", job_id)
    payload.setdefault("status", "created")
    payload.setdefault("progress", 0)
    payload.setdefault("created_at", datetime.utcnow().isoformat())
    payload.setdefault("meta", {})
    write_job(job_id, payload)
