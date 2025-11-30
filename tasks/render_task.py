# tasks/render_task.py
"""
Celery task that performs the rendering pipeline:
- loads job metadata
- calls engine.render_project(project_dict)
- assembles mp4
- uploads to S3 if configured
- updates job file (success or failure)
"""
import os
import json
import logging
import traceback
from pathlib import Path
from datetime import datetime
from services.celery_app import celery_app

logger = logging.getLogger("visora_render")
logging.basicConfig(level=logging.INFO)

BASE_DIR = Path(__file__).resolve().parent.parent
JOBS_DIR = BASE_DIR / "jobs"
OUTPUT_DIR = BASE_DIR / "public" / "videos"
JOBS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# try import your engine render function
TRY_ENGINE = None
try:
    # Expected API: engine.render_project(project_dict, out_path) -> path_to_mp4
    from engine.cinematic_engine import CinematicEngine  # example
    TRY_ENGINE = "cinematic"
    logger.info("Using cinematic_engine")
except Exception:
    try:
        from engine.render_engine import render_project as core_render
        TRY_ENGINE = "render_project"
        logger.info("Using render_engine.render_project")
    except Exception:
        TRY_ENGINE = None
        logger.warning("No render engine found. Implement engine.cinematic_engine.CinematicEngine or engine.render_engine.render_project")

# helper read/save job
def read_job(job_id: str):
    p = JOBS_DIR / f"{job_id}.json"
    if not p.exists():
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

def save_job(job_data: dict):
    p = JOBS_DIR / f"{job_data['id']}.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(job_data, f, ensure_ascii=False, indent=2)

# finalize helpers (also imported by app.py)
def finalize_job_success(job_id: str, local_out: str):
    job = read_job(job_id)
    if not job:
        logger.error("finalize_job_success: job not found %s", job_id)
        return False
    # try upload to s3 if config present
    s3_url = None
    try:
        from app import upload_to_s3_if_configured  # local import
        s3_key = f"videos/{job_id}.mp4"
        s3_url = upload_to_s3_if_configured(local_out, s3_key)
    except Exception:
        logger.exception("S3 upload helper failed")

    job["result"] = {"video_url": s3_url or f"{os.environ.get('BASE_URL','')}/public/videos/{job_id}.mp4"}
    job["status"] = "completed"
    job["completed_at"] = datetime.utcnow().isoformat()
    save_job(job)
    logger.info("Job finalized success %s -> %s", job_id, job["result"]["video_url"])
    return True

def finalize_job_failed(job_id: str, error_msg: str):
    job = read_job(job_id)
    if not job:
        logger.error("finalize_job_failed: job not found %s", job_id)
        return False
    job["status"] = "failed"
    job["error"] = error_msg
    job["completed_at"] = datetime.utcnow().isoformat()
    save_job(job)
    logger.info("Job finalized failed %s", job_id)
    return True

# Celery task
@celery_app.task(name="tasks.render_task.render_job_task", bind=True)
def render_job_task(self, job_id: str):
    logger.info("Starting render job %s", job_id)
    job = read_job(job_id)
    if not job:
        logger.error("Job not found %s", job_id)
        return {"ok": False, "error": "job_not_found"}

    # update job status
    job["status"] = "started"
    save_job(job)

    try:
        # prepare project dict expected by engine
        project = {
            "id": job["id"],
            "script": job.get("script_text") or job.get("script") or "",
            "preset": job.get("preset", "short"),
            "avatar": job.get("avatar"),
            "meta": job.get("meta", {}),
            "output": str(OUTPUT_DIR / f"{job_id}.mp4")
        }

        # choose engine call
        local_out = None
        if TRY_ENGINE == "cinematic":
            # example usage for CinematicEngine
            eng = CinematicEngine(work_dir=None, debug=False)
            local_out = eng.render_project(project)  # should return path to mp4
        elif TRY_ENGINE == "render_project":
            # direct function
            local_out = core_render(project)
        else:
            raise NotImplementedError("No rendering engine implemented on server.")

        # verify output exists
        if not local_out or not Path(local_out).exists():
            raise RuntimeError(f"Render did not produce output: {local_out}")

        # finalize success (uploads to S3 if configured)
        finalize_job_success(job_id, str(local_out))
        return {"ok": True, "job_id": job_id, "video": str(local_out)}

    except Exception as e:
        logger.exception("Render job failed %s", job_id)
        tb = traceback.format_exc()
        finalize_job_failed(job_id, f"{str(e)}\n{tb}")
        return {"ok": False, "job_id": job_id, "error": str(e)}
