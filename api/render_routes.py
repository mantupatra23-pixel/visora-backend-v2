from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from models import Job  # update based on your ORM
from services.queue import enqueue_render_job
from datetime import datetime
import logging

router = APIRouter()
logger = logging.getLogger("render_routes")

@router.get("/render/start/{job_id}")
async def start_render(job_id: str, request: Request):
    job = await Job.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status in ("started","parsing","rendering","completed"):
        raise HTTPException(status_code=409, detail=f"Job already {job.status}")

    job.status = "queued"
    job.meta = job.meta or {}
    job.meta["manual_started"] = True
    job.meta["manual_started_at"] = datetime.utcnow().isoformat()
    job.meta["manual_started_ip"] = request.client.host
    await job.save()

    enqueue_render_job(str(job.id))

    return JSONResponse({
        "ok": True,
        "job_id": str(job.id),
        "status": job.status
    })
