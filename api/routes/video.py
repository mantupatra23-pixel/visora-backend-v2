# api/routes/video.py
# If you prefer route modularization (optional). Example FastAPI APIRouter.
from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel
import uuid
import json
from pathlib import Path
from datetime import datetime
import os

router = APIRouter()

JOBS_DIR = Path(__file__).resolve().parent.parent / "jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)

class CreateVideoSchema(BaseModel):
    script: str
    preset: str = "short"
    avatar: str = None
    meta: dict = {}

@router.post("/create-video")
async def create_video(body: CreateVideoSchema):
    if not body.script or not body.script.strip():
        raise HTTPException(status_code=400, detail="script is required")
    jid = str(uuid.uuid4())
    job = {
        "id": jid,
        "script_text": body.script,
        "preset": body.preset,
        "avatar": body.avatar,
        "meta": body.meta,
        "status": "created",
        "created_at": datetime.utcnow().isoformat()
    }
    p = JOBS_DIR / f"{jid}.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(job, f, ensure_ascii=False, indent=2)
    # enqueue via celery if available
    try:
        from services.celery_app import enqueue_render_job
        enqueue_render_job(jid)
    except Exception:
        pass
    return {"ok": True, "job_id": jid, "status": "created"}
