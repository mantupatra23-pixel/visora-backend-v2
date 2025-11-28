from fastapi import APIRouter
from models import Job
from services.celery_app import celery_app

router = APIRouter()

@router.get("/admin/jobs")
async def list_jobs():
    jobs = Job.find_many(limit=200, skip=0)
    return {"jobs": [j.to_dict() for j in jobs]}

@router.get("/admin/workers")
async def workers():
    insp = celery_app.control.inspect()
    return {
        "stats": insp.stats() or {},
        "active": insp.active() or {},
    }

@router.get("/admin/queue")
async def queue_info():
    insp = celery_app.control.inspect()
    return {
        "reserved": insp.reserved() or {},
        "scheduled": insp.scheduled() or {},
    }
