# app.py
"""
Visora Backend - app.py (production-ready)
Provides:
 - POST /create-video       -> create job (async)
 - GET  /render/start/<id>  -> enqueue job manually
 - GET  /job/<id>           -> status + progress + video_url
 - GET  /download/<id>      -> download final mp4 or structured error
 - GET  /health             -> simple health check
 - Admin routes:
    - GET /admin/jobs
    - POST /admin/clear-failed
    - GET /admin/workers (basic)
Config via env vars.
"""

import os
import json
import uuid
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from flask import Flask, request, jsonify, send_from_directory, abort
from flask_cors import CORS
from celery import Celery
import requests

# ---------------------------
# Logging
# ---------------------------
logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger("visora.app")

# ---------------------------
# Config / Env
# ---------------------------
REDIS_URL = os.environ.get("REDIS_URL", "")
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/") or os.environ.get("PUBLIC_URL", "").rstrip("/")
VIDEO_SAVE_DIR = Path(os.environ.get("VIDEO_SAVE_DIR", "public/videos"))
JOBS_DIR = Path(os.environ.get("JOBS_DIR", "jobs"))
S3_BUCKET = os.environ.get("S3_BUCKET", "")
AWS_REGION = os.environ.get("AWS_REGION", "")
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "")
CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*")  # comma separated OR "*"
JOB_RETENTION_DAYS = int(os.environ.get("JOB_RETENTION_DAYS", "7"))
VIDEO_RETENTION_DAYS = int(os.environ.get("VIDEO_RETENTION_DAYS", "30"))

# Ensure dirs exist
VIDEO_SAVE_DIR.mkdir(parents=True, exist_ok=True)
JOBS_DIR.mkdir(parents=True, exist_ok=True)

# Celery client (used only to send tasks to broker)
# We create a lightweight Celery instance to send tasks.
def make_celery(broker_url: str):
    celery = Celery("visora_client", broker=broker_url, backend=broker_url)
    # recommended kombu/celery config tuning can be added here
    celery.conf.update({
        "task_serializer": "json",
        "result_serializer": "json",
        "accept_content": ["json"],
        "timezone": "UTC",
        "enable_utc": True,
    })
    return celery

if not REDIS_URL:
    LOG.warning("REDIS_URL not set. Celery will not be able to enqueue tasks until REDIS_URL provided.")

celery = make_celery(REDIS_URL) if REDIS_URL else None

# ---------------------------
# Flask app
# ---------------------------
app = Flask(__name__, static_folder=str(VIDEO_SAVE_DIR.parent), static_url_path="/")
# CORS configuration
if CORS_ORIGINS.strip() == "*" or not CORS_ORIGINS:
    cors = CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)
else:
    origins = [o.strip() for o in CORS_ORIGINS.split(",")]
    cors = CORS(app, resources={r"/*": {"origins": origins}}, supports_credentials=True)

# ---------------------------
# Helpers
# ---------------------------
def job_file_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"

def write_job(job_id: str, data: Dict[str, Any]) -> None:
    p = job_file_path(job_id)
    # ensure meta structure
    data.setdefault("id", job_id)
    data.setdefault("status", data.get("status", "created"))
    data.setdefault("meta", data.get("meta", {}))
    data["meta"].setdefault("updated_at", datetime.utcnow().isoformat())
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    LOG.debug("Wrote job file: %s", p)

def read_job(job_id: str) -> Dict[str, Any]:
    p = job_file_path(job_id)
    if not p.exists():
        raise FileNotFoundError("Job not found")
    return json.loads(p.read_text(encoding="utf-8"))

def create_job_record(script: str, preset: str = "reel", face_video: Optional[str] = None, meta: Optional[Dict]=None) -> str:
    job_id = str(uuid.uuid4())
    data = {
        "id": job_id,
        "status": "created",
        "progress": 0,
        "script": script,
        "preset": preset,
        "face_video": face_video,
        "created_at": datetime.utcnow().isoformat(),
        "meta": meta or {}
    }
    write_job(job_id, data)
    return job_id

def enqueue_job(job_id: str) -> Dict[str, Any]:
    """
    Enqueue the job to celery worker via send_task.
    Worker task name must match tasks.render_task.render_job_task
    """
    if not celery:
        raise RuntimeError("Celery broker not configured (REDIS_URL missing).")
    # update job status to queued
    job = read_job(job_id)
    job["status"] = "queued"
    job["meta"]["queued_at"] = datetime.utcnow().isoformat()
    write_job(job_id, job)

    task_name = "tasks.render_task.render_job_task"  # must match worker-side task name
    # send task asynchronously
    LOG.info("Enqueuing job %s to queue 'renderers' via task %s", job_id, task_name)
    result = celery.send_task(task_name, args=[job_id], queue="renderers", kwargs={})
    return {"task_id": getattr(result, "id", None)}

def safe_json_error(code:int, err_code:str, message:str):
    return jsonify({"error": err_code, "message": message}), code

def make_video_url(job_id: str) -> str:
    # strictly format: https://<base>/videos/<job_id>.mp4
    base = BASE_URL or request.host_url.rstrip("/")
    return f"{base}/videos/{job_id}.mp4"

# Progress mapping helper (read job meta if worker updates progress)
def get_progress_from_job(job: Dict[str, Any]) -> int:
    # prefer explicit progress field
    p = job.get("progress")
    if isinstance(p, int):
        return max(0, min(100, p))
    # fallback to stage mapping
    stage = job.get("status", "")
    mapping = {
        "created": 0,
        "queued": 2,
        "started": 5,
        "tts": 15,
        "lipsync": 45,
        "rendering": 70,
        "combining": 85,
        "uploading": 92,
        "completed": 100,
        "failed": 100
    }
    return mapping.get(stage, 0)

# ---------------------------
# Routes
# ---------------------------

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status":"ok"}), 200

@app.route("/create-video", methods=["POST"])
def create_video():
    """
    Accept: {"script": "...", "preset": "reel"|"short", "face_video": optional, "meta": {}}
    Returns: {id, status, job_id}
    This route ONLY creates job and returns immediately.
    """
    try:
        data = request.get_json(force=True)
    except Exception:
        return safe_json_error(400, "invalid_json", "Request body must be JSON.")

    script = data.get("script", "") or data.get("script_text", "")
    if not script or not isinstance(script, str):
        return safe_json_error(400, "missing_script", "Field 'script' is required.")

    preset = data.get("preset", "reel")
    face_video = data.get("face_video")
    meta = data.get("meta", {})

    # create job record
    job_id = create_job_record(script=script, preset=preset, face_video=face_video, meta=meta)

    # Optionally auto-enqueue (async) — prefer to auto start
    try:
        enqueue_job(job_id)
    except Exception as e:
        LOG.exception("Failed to enqueue job automatically")
        # job remains in created state; return job_id and let client call /render/start/<id>
        return jsonify({"job_id": job_id, "ok": True, "status": "created", "warning": "enqueue_failed", "message": str(e)}), 202

    return jsonify({"job_id": job_id, "ok": True, "status": "queued"}), 202

@app.route("/render/start/<job_id>", methods=["GET","POST"])
def render_start(job_id: str):
    """
    Manual start: if job exists and is not queued/started, queue it.
    """
    try:
        job = read_job(job_id)
    except FileNotFoundError:
        return safe_json_error(404, "not_found", "Job not found.")
    if job.get("status") in ("queued", "started", "processing", "tts", "lipsync", "rendering", "combining"):
        return safe_json_error(409, "already_started", "Job already started or queued.")
    try:
        enqueue_job(job_id)
    except Exception as e:
        LOG.exception("Failed to enqueue job %s", job_id)
        return safe_json_error(500, "enqueue_failed", str(e))
    return jsonify({"job_id": job_id, "ok": True, "status": "queued"}), 202

@app.route("/job/<job_id>", methods=["GET"])
def job_status(job_id: str):
    """
    Stable response format:
    {id, status, progress, video_url, error, meta}
    """
    try:
        job = read_job(job_id)
    except FileNotFoundError:
        return safe_json_error(404, "not_found", "Job not found.")
    status = job.get("status", "created")
    progress = get_progress_from_job(job)
    video_url = None
    if status == "completed":
        # final path deterministic
        video_url = make_video_url(job_id)
    ret = {
        "id": job_id,
        "status": status,
        "progress": progress,
        "video_url": video_url,
        "meta": job.get("meta", {})
    }
    if job.get("error"):
        ret["error"] = job.get("error")
    return jsonify(ret), 200

@app.route("/download/<job_id>", methods=["GET"])
def download_video(job_id: str):
    """
    Serve file if exists in VIDEO_SAVE_DIR with name <job_id>.mp4
    """
    file_name = f"{job_id}.mp4"
    file_path = VIDEO_SAVE_DIR / file_name
    if not file_path.exists():
        # check job state
        try:
            job = read_job(job_id)
            status = job.get("status", "created")
            if status != "completed":
                return safe_json_error(409, "not_ready", f"Job not ready. Current status: {status}")
        except FileNotFoundError:
            return safe_json_error(404, "not_found", "Job not found.")
        return safe_json_error(404, "file_missing", "Final video file not found on server.")
    # stream file with send_from_directory
    # VIDEO_SAVE_DIR should be absolute or relative; use send_from_directory
    return send_from_directory(directory=str(VIDEO_SAVE_DIR), filename=file_name, as_attachment=True, mimetype="video/mp4")

# ---------------------------
# Admin endpoints (protected by ADMIN_API_KEY)
# ---------------------------

def require_admin():
    key = request.headers.get("X-ADMIN-KEY") or request.args.get("admin_key")
    if not ADMIN_API_KEY:
        LOG.warning("ADMIN_API_KEY not configured; denying admin access")
        abort(403)
    if not key or key != ADMIN_API_KEY:
        abort(403)

@app.route("/admin/jobs", methods=["GET"])
def admin_list_jobs():
    require_admin()
    jobs: List[Dict[str,Any]] = []
    for p in sorted(JOBS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
            jobs.append({
                "id": j.get("id"),
                "status": j.get("status"),
                "created_at": j.get("created_at"),
                "meta": j.get("meta", {})
            })
        except Exception:
            LOG.exception("Failed read job file %s", p)
    return jsonify({"jobs": jobs}), 200

@app.route("/admin/clear-failed", methods=["POST"])
def admin_clear_failed():
    require_admin()
    removed = []
    for p in JOBS_DIR.glob("*.json"):
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
            if j.get("status") == "failed":
                # optionally remove associated video if exists
                vid = VIDEO_SAVE_DIR / f"{j.get('id')}.mp4"
                try:
                    if vid.exists():
                        vid.unlink()
                except Exception:
                    LOG.exception("Failed remove video %s", vid)
                p.unlink()
                removed.append(j.get("id"))
        except Exception:
            LOG.exception("Error clearing job %s", p)
    return jsonify({"removed": removed}), 200

@app.route("/admin/workers", methods=["GET"])
def admin_workers():
    require_admin()
    # best-effort: try to query Celery broker for active workers
    info = {"connected": False, "workers": []}
    try:
        if celery:
            insp = celery.control.inspect(timeout=1.0)
            active = insp.active() or {}
            registered = insp.registered() or {}
            stats = insp.stats() or {}
            info["connected"] = True
            for w, tasks in (active.items() if isinstance(active, dict) else []):
                info["workers"].append({"worker": w, "active_tasks": len(tasks or [])})
            # include stats if available
            info["stats"] = stats
    except Exception as e:
        LOG.warning("Failed to inspect celery workers: %s", e)
    return jsonify(info), 200

# ---------------------------
# Utilities: cleanup old jobs and videos (can be called by cron/admin)
# ---------------------------
@app.route("/admin/cleanup", methods=["POST"])
def admin_cleanup():
    require_admin()
    now = datetime.utcnow()
    removed_jobs = []
    removed_videos = []
    # jobs older than JOB_RETENTION_DAYS
    cutoff_jobs = now - timedelta(days=JOB_RETENTION_DAYS)
    for p in JOBS_DIR.glob("*.json"):
        try:
            stat = p.stat()
            if datetime.utcfromtimestamp(stat.st_mtime) < cutoff_jobs:
                # remove job and any associated video
                try:
                    j = json.loads(p.read_text(encoding="utf-8"))
                    vid = VIDEO_SAVE_DIR / f"{j.get('id')}.mp4"
                    if vid.exists():
                        vid.unlink()
                        removed_videos.append(str(vid))
                except Exception:
                    LOG.exception("While cleaning job %s", p)
                p.unlink()
                removed_jobs.append(str(p))
        except Exception:
            LOG.exception("Error cleaning %s", p)
    return jsonify({"removed_jobs": removed_jobs, "removed_videos": removed_videos}), 200

# ---------------------------
# Error handlers - structured JSON
# ---------------------------
@app.errorhandler(400)
def err_400(e):
    return jsonify({"error":"bad_request","message": str(e)}), 400

@app.errorhandler(403)
def err_403(e):
    return jsonify({"error":"forbidden","message":"Admin auth failed"}), 403

@app.errorhandler(404)
def err_404(e):
    return jsonify({"error":"not_found","message": str(e)}), 404

@app.errorhandler(500)
def err_500(e):
    LOG.exception("Server error: %s", e)
    return jsonify({"error":"server_error","message":"Internal server error"}), 500

# ---------------------------
# Run (for local dev) - in production use gunicorn
# ---------------------------
if __name__ == "__main__":
    LOG.info("Starting Visora backend (dev mode). BASE_URL=%s REDIS=%s VIDEO_DIR=%s", BASE_URL, bool(REDIS_URL), VIDEO_SAVE_DIR)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
