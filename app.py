#!/usr/bin/env python3
# app.py - Production-ready Visora backend main app
# Uses file-based job store by default (jobs/<job_id>.json) and OUTPUT_DIR = public/videos
# Expects Celery worker (tasks.render_job_task) to be configured separately.

import os
import json
import uuid
import logging
import traceback
from pathlib import Path
from datetime import datetime
from typing import Optional
from flask import Flask, request, jsonify, send_file, abort
from flask_cors import CORS

# ---- CONFIG / ENV ----
BASE_DIR = Path(__file__).resolve().parent
JOBS_DIR = Path(os.environ.get("JOBS_DIR", BASE_DIR / "jobs"))
OUTPUT_DIR = Path(os.environ.get("VIDEO_SAVE_DIR", BASE_DIR / "public" / "videos"))
JOBS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")  # e.g. https://visora-ai-yclw.onrender.com
S3_BUCKET = os.environ.get("S3_BUCKET", "")
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")

CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()]
if not CORS_ORIGINS:
    CORS_ORIGINS = ["*"]

REDIS_URL = os.environ.get("REDIS_URL", "")  # e.g. rediss://default:...@host:6379/?ssl_cert_reqs=CERT_NONE

# Allowed statuses (consistent)
ALLOWED_STATUSES = ["created", "queued", "started", "tts", "lipsync", "rendering", "combining", "completed", "failed"]

# Logging
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger("visora_app")

# ---- Flask init ----
app = Flask(__name__, static_folder=str(BASE_DIR / "public"), static_url_path="/public")
CORS(app, resources={r"/*": {"origins": CORS_ORIGINS}}, supports_credentials=True)

# ---- Utils: job file handling (simple file store) ----
def job_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"

def read_job(job_id: str) -> Optional[dict]:
    p = job_path(job_id)
    if not p.exists():
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        logger.exception("Failed to read job file %s", p)
        return None

def write_job(job: dict):
    p = job_path(job["id"])
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(job, f, ensure_ascii=False, indent=2)
    except Exception:
        logger.exception("Failed to write job %s", job.get("id"))

def create_job_entry(script_text: str, preset: str = "short", avatar: Optional[str] = None, meta: dict = None, render_settings: dict = None) -> dict:
    jid = str(uuid.uuid4())
    job = {
        "id": jid,
        "script_text": script_text,
        "preset": preset,
        "avatar": avatar,
        "meta": meta or {},
        "render_settings": render_settings or {},
        "status": "created",
        "progress": 0,
        "result": {},
        "created_at": datetime.utcnow().isoformat() + "Z",
        "completed_at": None,
        "error": None
    }
    write_job(job)
    return job

def set_job_status(job_id: str, status: str, progress: Optional[int] = None, extra_meta: dict = None):
    job = read_job(job_id) or {"id": job_id}
    job["status"] = status
    if progress is not None:
        job["progress"] = max(0, min(100, int(progress)))
    job.setdefault("meta", {})
    if extra_meta:
        job["meta"].update(extra_meta)
    job["last_update_at"] = datetime.utcnow().isoformat() + "Z"
    write_job(job)
    logger.info("Job %s status -> %s (%s%%)", job_id, status, job.get("progress"))

def set_job_result(job_id: str, video_url: str):
    job = read_job(job_id) or {"id": job_id}
    job["result"] = {"video_url": video_url}
    job["status"] = "completed"
    job["progress"] = 100
    job["completed_at"] = datetime.utcnow().isoformat() + "Z"
    write_job(job)
    logger.info("Job %s completed -> %s", job_id, video_url)

def set_job_failed(job_id: str, error: str):
    job = read_job(job_id) or {"id": job_id}
    job["status"] = "failed"
    job["error"] = error
    job["completed_at"] = datetime.utcnow().isoformat() + "Z"
    write_job(job)
    logger.error("Job %s failed: %s", job_id, error)

# ---- Celery queue helper (fallback) ----
# We attempt to import enqueuer function from your services.queue. If not present, we'll try to call celery task name.
enqueue_render_job = None
try:
    from services.queue import enqueue_render_job as _enq
    enqueue_render_job = _enq
    logger.info("Loaded services.queue.enqueue_render_job")
except Exception:
    enqueue_render_job = None
    logger.info("services.queue.enqueue_render_job not found, using fallback send_task approach")

# Try to import celery app for fallback
celery_app = None
try:
    from services.celery_app import celery_app as _cel
    celery_app = _cel
    logger.info("Loaded services.celery_app")
except Exception:
    celery_app = None

def fallback_enqueue(job_id: str) -> bool:
    """
    Fallback: use celery_app to send_task 'tasks.render_task.render_job_task' (or configured name).
    Returns True if enqueued.
    """
    if celery_app:
        try:
            # name should match the task name in tasks/render_task.py
            celery_app.send_task("tasks.render_task.render_job_task", args=[job_id], queue="celery")
            logger.info("Fallback enqueued job %s via celery_app.send_task", job_id)
            return True
        except Exception as e:
            logger.exception("Failed fallback enqueue: %s", e)
            return False
    logger.error("No enqueue available (no services.queue and no celery_app)")
    return False

# ---- S3 helper (optional) ----
def upload_to_s3_if_configured(local_path: str, s3_key: str) -> Optional[str]:
    if not (S3_BUCKET and AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY):
        logger.debug("S3 not configured, skipping upload")
        return None
    try:
        import boto3
        s3 = boto3.client("s3", aws_access_key_id=AWS_ACCESS_KEY_ID,
                          aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
                          region_name=AWS_REGION)
        s3.upload_file(local_path, S3_BUCKET, s3_key, ExtraArgs={'ACL': 'public-read', 'ContentType': 'video/mp4'})
        url = f"https://{S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{s3_key}"
        logger.info("Uploaded %s -> %s", local_path, url)
        return url
    except Exception:
        logger.exception("S3 upload failed")
        return None

# ---- ROUTES ----
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "status": "ok", "time": datetime.utcnow().isoformat() + "Z"})

@app.route("/create-video", methods=["POST"])
def create_video():
    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            return jsonify({"error": "invalid_json", "message": "Invalid JSON"}), 400
        script_text = data.get("script") or data.get("script_text") or ""
        preset = data.get("preset", "short")
        avatar = data.get("avatar")
        meta = data.get("meta", {})
        render_settings = data.get("render_settings", {})

        if not script_text or not script_text.strip():
            return jsonify({"error": "script_required", "message": "script text required"}), 400

        job = create_job_entry(script_text=script_text, preset=preset, avatar=avatar, meta=meta, render_settings=render_settings)
        logger.info("Created job %s preset=%s", job["id"], preset)

        # set status created (already set), return job id quickly
        return jsonify({"ok": True, "job_id": job["id"], "status": job["status"]})
    except Exception as e:
        logger.exception("create_video failed")
        return jsonify({"error": "create_failed", "message": str(e)}), 500

@app.route("/render/start/<job_id>", methods=["POST", "GET"])
def start_render(job_id):
    try:
        job = read_job(job_id)
        if not job:
            return jsonify({"error": "job_not_found", "message": "Job file not found"}), 404

        # prevent duplicate start
        if job.get("status") in ("queued", "started", "tts", "lipsync", "rendering", "combining"):
            return jsonify({"error": "already_started", "message": "Job already started or queued."}), 400

        # mark queued
        set_job_status(job_id, "queued", progress=1)

        # enqueue using preferred function
        enqueued = False
        if enqueue_render_job:
            try:
                enqueue_render_job(job_id)
                enqueued = True
                logger.info("Enqueued job %s via services.queue", job_id)
            except Exception:
                logger.exception("enqueue_render_job failed")
                enqueued = False

        if not enqueued:
            enqueued = fallback_enqueue(job_id)

        if not enqueued:
            set_job_failed(job_id, "enqueue_failed")
            return jsonify({"ok": False, "error": "enqueue_failed", "message": "Failed to enqueue job"}), 500

        return jsonify({"ok": True, "job_id": job_id, "status": "queued"})
    except Exception:
        logger.exception("start_render error")
        return jsonify({"error": "start_failed", "message": "internal"}), 500

@app.route("/job/<job_id>", methods=["GET"])
def job_status(job_id):
    job = read_job(job_id)
    if not job:
        return jsonify({"error": "job_not_found", "message": "Job not found"}), 404
    # ensure stable format
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    video_url = result.get("video_url") or (job.get("result") if isinstance(job.get("result"), str) else None)
    resp = {
        "id": job.get("id"),
        "status": job.get("status", "created"),
        "progress": job.get("progress", 0),
        "video_url": video_url or "",
        "error": job.get("error")
    }
    return jsonify(resp)

@app.route("/download/<job_id>", methods=["GET"])
def download(job_id):
    try:
        job = read_job(job_id)
        if not job:
            return jsonify({"error": "job_not_found", "message": "Job not found"}), 404

        # prefer explicit result.video_url (S3 or absolute)
        if job.get("result") and isinstance(job.get("result"), dict) and job["result"].get("video_url"):
            vurl = job["result"]["video_url"]
            # if it's an external URL, return it
            if vurl.startswith("http://") or vurl.startswith("https://"):
                return jsonify({"video_url": vurl})

        final_local = OUTPUT_DIR / f"{job_id}.mp4"
        if final_local.exists():
            # Serve file directly (Flask send_file)
            try:
                # Ensure correct mime
                return send_file(str(final_local), mimetype="video/mp4", as_attachment=False)
            except Exception:
                logger.exception("send_file failed for %s", final_local)
                return jsonify({"error": "send_failed", "message": "failed to send file"}), 500

        # fallback: if job completed but file not present -> check S3 url in result
        if job.get("status") == "completed" and job.get("result") and job["result"].get("video_url"):
            return jsonify({"video_url": job["result"]["video_url"]})

        # still processing
        return jsonify({"error": "not_ready", "message": "File not found — job processing or missing"}), 404
    except Exception:
        logger.exception("download error")
        return jsonify({"error": "internal", "message": "internal"}), 500

# ---- Admin endpoints ----
def require_admin():
    key = request.headers.get("x-api-key") or request.args.get("api_key")
    if not ADMIN_API_KEY or key != ADMIN_API_KEY:
        abort(401, description="Unauthorized")

@app.route("/admin/jobs", methods=["GET"])
def admin_list_jobs():
    require_admin()
    try:
        items = []
        files = sorted(JOBS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        limit = int(request.args.get("limit", 200))
        skip = int(request.args.get("skip", 0))
        for p in files[skip: skip + limit]:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    items.append(json.load(f))
            except Exception:
                continue
        return jsonify({"count": len(items), "jobs": items})
    except Exception:
        logger.exception("admin_list_jobs failed")
        return jsonify({"error": "internal"}), 500

@app.route("/admin/queue", methods=["GET"])
def admin_queue_info():
    require_admin()
    try:
        if celery_app:
            insp = celery_app.control.inspect()
            data = {
                "active": insp.active() or {},
                "reserved": insp.reserved() or {},
                "scheduled": insp.scheduled() or {},
                "registered": insp.registered() or {}
            }
            return jsonify(data)
        return jsonify({"error": "no_celery", "message": "Celery not configured"}), 500
    except Exception:
        logger.exception("queue inspect failed")
        return jsonify({"error": "internal"}), 500

@app.route("/admin/workers", methods=["GET"])
def admin_workers():
    require_admin()
    try:
        if celery_app:
            insp = celery_app.control.inspect()
            stats = insp.stats() or {}
            return jsonify(stats)
        return jsonify({"error": "no_celery", "message": "Celery not configured"}), 500
    except Exception:
        logger.exception("admin_workers failed")
        return jsonify({"error": "internal"}), 500

@app.route("/admin/clear-failed", methods=["POST"])
def admin_clear_failed():
    require_admin()
    try:
        removed = []
        for p in JOBS_DIR.glob("*.json"):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("status") == "failed":
                    p.unlink()
                    removed.append(p.name)
            except Exception:
                continue
        return jsonify({"ok": True, "removed": removed})
    except Exception:
        logger.exception("clear failed error")
        return jsonify({"error": "internal"}), 500

# ---- Utility: Validate REDIS_URL for Upstash style usage ----
@app.before_first_request
def check_redis_url():
    if REDIS_URL and REDIS_URL.startswith("rediss://"):
        # Acceptable params: ?ssl_cert_reqs=CERT_NONE  or CERT_OPTIONAL or CERT_REQUIRED
        if "ssl_cert_reqs=" not in REDIS_URL:
            logger.warning("REDIS_URL uses rediss:// but missing ssl_cert_reqs param. Upstash requires ssl_cert_reqs parameter (e.g. ?ssl_cert_reqs=CERT_NONE)")

# ---- Error handlers to return structured JSON ----
@app.errorhandler(400)
def bad_request(e):
    return jsonify({"error": "bad_request", "message": str(e)}), 400

@app.errorhandler(401)
def unauthorized(e):
    return jsonify({"error": "unauthorized", "message": str(e)}), 401

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "not_found", "message": str(e)}), 404

@app.errorhandler(500)
def internal_error(e):
    # log traceback server side, but return minimal message to client
    logger.exception("Internal server error: %s", e)
    return jsonify({"error": "internal", "message": "internal server error"}), 500

# ---- Run (for dev only) ----
if __name__ == "__main__":
    # For production use: gunicorn app:app --workers 1 --threads 8 --timeout 120
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
