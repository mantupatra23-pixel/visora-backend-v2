# app.py
"""
Main FastAPI backend for Visora-style video renderer.
Provides:
- POST /create-video  -> create job
- GET  /job/{job_id}  -> job status/meta
- GET  /download/{job_id} -> serve final mp4 or JSON with URL
- Admin endpoints (protected by ADMIN_API_KEY)
"""
import os
import json
import uuid
import logging
import traceback
from pathlib import Path
from typing import Optional, Dict
from fastapi import FastAPI, Request, HTTPException, Header, Depends
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# config
BASE_DIR = Path(__file__).resolve().parent
JOBS_DIR = BASE_DIR / "jobs"
OUTPUT_DIR = BASE_DIR / "public" / "videos"
JOBS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "")
BASE_URL = os.environ.get("BASE_URL", "https://example.com")
S3_BUCKET = os.environ.get("S3_BUCKET", "")
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
BACON_URL = os.environ.get("BACON_URL", "")
CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()]

# logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("visora_app")

# try to import enqueue function (Celery)
enqueue_render_job = None
try:
    from services.celery_app import enqueue_render_job as _enqueue
    enqueue_render_job = _enqueue
    logger.info("Loaded Celery enqueue function")
except Exception:
    logger.warning("Celery enqueue function not available; jobs will be created but not queued")

# utility Job model stored as JSON file
class Job:
    storage_dir = JOBS_DIR

    def __init__(self, id: str, script_text: str = "", preset: str = "short", avatar=None,
                 meta: Optional[dict] = None, result: Optional[dict] = None,
                 status: str = "created", created_at: Optional[str] = None, completed_at: Optional[str] = None):
        self.id = id
        self.script_text = script_text
        self.preset = preset
        self.avatar = avatar
        self.meta = meta or {}
        self.result = result or {}
        self.status = status
        self.created_at = created_at
        self.completed_at = completed_at
        self.error = None

    @property
    def path(self):
        return self.storage_dir / f"{self.id}.json"

    def to_dict(self):
        return {
            "id": self.id,
            "script_text": self.script_text,
            "preset": self.preset,
            "avatar": self.avatar,
            "meta": self.meta,
            "result": self.result,
            "status": self.status,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "error": self.error
        }

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception:
            logger.exception("Failed saving job")
            raise

    @classmethod
    def get(cls, id: str):
        p = cls.storage_dir / f"{id}.json"
        if not p.exists():
            return None
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            job = Job(
                id=data.get("id"),
                script_text=data.get("script_text", ""),
                preset=data.get("preset", "short"),
                avatar=data.get("avatar"),
                meta=data.get("meta", {}),
                result=data.get("result", {}),
                status=data.get("status", "created"),
                created_at=data.get("created_at"),
                completed_at=data.get("completed_at")
            )
            job.error = data.get("error")
            return job
        except Exception:
            logger.exception("Failed reading job file")
            return None

# import finalize helpers from tasks module (they update job)
try:
    from tasks.render_task import finalize_job_success, finalize_job_failed
except Exception:
    # define simple placeholders so API doesn't crash
    def finalize_job_success(job_id: str, local_out: str):
        logger.info("finalize_job_success placeholder called for %s", job_id)
    def finalize_job_failed(job_id: str, error_msg: str):
        logger.error("finalize_job_failed placeholder %s %s", job_id, error_msg)

# FastAPI app
app = FastAPI(title="Visora Backend")
if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS if CORS_ORIGINS != ["*"] else ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Request body model
class CreateVideoRequest(BaseModel):
    script: str
    preset: Optional[str] = "short"
    avatar: Optional[str] = None
    meta: Optional[Dict] = {}

# Utility: upload to S3 (optional)
def upload_to_s3_if_configured(local_path: str, s3_key: str) -> Optional[str]:
    if not (S3_BUCKET and AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY):
        logger.info("S3 not configured; skipping upload")
        return None
    try:
        import boto3
        s3 = boto3.client(
            "s3",
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=AWS_REGION,
        )
        s3.upload_file(local_path, S3_BUCKET, s3_key, ExtraArgs={"ACL": "public-read"})
        url = f"https://{S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{s3_key}"
        logger.info("Uploaded to S3: %s", url)
        return url
    except Exception:
        logger.exception("S3 upload failed")
        return None

# Health
@app.get("/health")
async def health():
    return {"ok": True, "time": __import__("datetime").datetime.utcnow().isoformat(), "jobs_dir": str(JOBS_DIR), "output_dir": str(OUTPUT_DIR)}

# Create video (public)
@app.post("/create-video")
async def create_video(body: CreateVideoRequest, request: Request):
    try:
        data = body.dict()
        script = data.get("script")
        if not script or not script.strip():
            raise HTTPException(status_code=400, detail="script is required")

        preset = data.get("preset", "short")
        avatar = data.get("avatar")
        meta = data.get("meta", {})

        jid = str(uuid.uuid4())
        job = Job(id=jid, script_text=script, preset=preset, avatar=avatar, meta=meta, status="created")
        job.created_at = __import__("datetime").datetime.utcnow().isoformat()
        job.save()
        logger.info("Created job %s preset=%s", jid, preset)

        # try enqueue
        if enqueue_render_job:
            try:
                enqueue_render_job(str(job.id))
            except Exception:
                logger.exception("enqueue_render_job failed")
                # don't fail create; return created with warning
                return JSONResponse(status_code=201, content={"ok": True, "job_id": jid, "status": "created", "warning": "enqueue_failed"})
        else:
            logger.warning("No enqueue function configured")

        return JSONResponse(status_code=201, content={"ok": True, "job_id": jid, "status": "created"})
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("create_video failed")
        raise HTTPException(status_code=500, detail=str(e))

# Manual start (admin)
def require_admin(x_api_key: Optional[str] = Header(None)):
    if ADMIN_API_KEY and x_api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True

@app.get("/render/start/{job_id}")
async def start_render(job_id: str, admin: bool = Depends(require_admin)):
    job = Job.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status in ("started", "queued", "parsing"):
        return {"error": "already_started", "message": "Job already started or queued."}
    job.status = "queued"
    job.save()
    if enqueue_render_job:
        try:
            enqueue_render_job(job_id)
            return {"ok": True, "job_id": job_id, "status": "queued"}
        except Exception:
            logger.exception("enqueue failed")
            return {"ok": True, "job_id": job_id, "status": "queued", "warning": "enqueue_failed"}
    else:
        return {"ok": True, "job_id": job_id, "status": "queued", "warning": "no_enqueue"}

@app.get("/job/{job_id}")
async def job_status(job_id: str):
    job = Job.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job": job.to_dict()}

@app.get("/download/{job_id}")
async def download(job_id: str):
    job = Job.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    # prefer explicit url from job.result
    if job.result and job.result.get("video_url"):
        return {"video_url": job.result.get("video_url")}
    # else check local file
    final_local = OUTPUT_DIR / f"{job_id}.mp4"
    if final_local.exists():
        # serve file
        return FileResponse(str(final_local), media_type="video/mp4", filename=f"{job_id}.mp4")
    # fallback: public URL
    public = f"{BASE_URL}/download/{job_id}.mp4"
    return JSONResponse(status_code=404, content={"error": "Output not ready", "public_fallback": public})

# Admin: list jobs
@app.get("/admin/jobs")
async def admin_list_jobs(admin: bool = Depends(require_admin)):
    items = []
    files = sorted(Job.storage_dir.glob("*.json"))
    for p in files[:200]:
        try:
            with open(p, "r", encoding="utf-8") as f:
                items.append(json.load(f))
        except Exception:
            continue
    return {"count": len(items), "jobs": items}
