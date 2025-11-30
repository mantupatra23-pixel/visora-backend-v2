# app.py
"""
Production-ready Visora backend main app (single-file).
Features implemented:
- POST /create-video  -> create job immediately and return job_id (async)
- GET  /render/start/<job_id> -> mark queued and enqueue celery worker
- GET  /job/<job_id>  -> stable JSON {id,status,progress,video_url}
- GET  /download/<job_id> -> serve final mp4 or return structured errors
- GET  /health -> simple health check
- Admin endpoints: /admin/jobs, /admin/queue, /admin/workers, /admin/clear-failed
- CORS configured via env CORS_ORIGINS
- Redis rediss:// handling (adds ssl_cert_reqs if needed)
- Structured error handling, logging, file storage under ./public/videos
- Optional S3 upload helper (upload_to_s3_if_configured)
- Uses simple JSON "job files" in JOBS_DIR to persist state
- Async behavior: create returns immediately; enqueue_render_job used to queue Celery task
"""

import os
import json
import uuid
import logging
import traceback
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from flask import Flask, request, jsonify, send_file, abort
from flask_cors import CORS

# ---------- CONFIG (env) ----------
BASE_DIR = Path(__file__).resolve().parent
JOBS_DIR = Path(os.environ.get("JOBS_DIR", str(BASE_DIR / "jobs")))
OUTPUT_DIR = Path(os.environ.get("VIDEO_SAVE_DIR", str(BASE_DIR / "public" / "videos")))
JOBS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")  # e.g. https://visora-ai-yclw.onrender.com
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "")
S3_BUCKET = os.environ.get("S3_BUCKET", "")
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
AWS_REGION = os.environ.get("AWS_REGION", "")

CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "")  # comma separated
ALLOWED_PRESETS = ["reel", "short", "cinematic"]

# Redis / Celery config
REDIS_URL = os.environ.get("REDIS_URL", os.environ.get("CELERY_BROKER_URL", ""))
# Upstash / rediss often requires ssl_cert_reqs param in URL for some clients.
# If scheme is rediss:// and url has no ssl_cert_reqs, append CERT_NONE to avoid validation errors in simple envs.
if REDIS_URL.startswith("rediss://") and "ssl_cert_reqs" not in REDIS_URL:
    # 注意: Upstash sometimes requires ?ssl_cert_reqs=CERT_NONE
    connector = "&" if "?" in REDIS_URL else "?"
    REDIS_URL = REDIS_URL + connector + "ssl_cert_reqs=CERT_NONE"

# ---------- Logging ----------
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger("visora_app")

# ---------- Flask app ----------
app = Flask(__name__, static_folder=str(BASE_DIR / "public"))
# configure CORS
if CORS_ORIGINS:
    origins = [o.strip() for o in CORS_ORIGINS.split(",") if o.strip()]
else:
    origins = ["*"]  # default allow all if not configured
CORS(app, resources={r"/*": {"origins": origins, "methods": ["GET", "POST", "OPTIONS"]}})
logger.info("CORS enabled for origins: %s", origins)

# ---------- Celery enqueue function (optional) ----------
enqueue_render_job = None
try:
    # Your services.queue should expose enqueue_render_job(job_id: str)
    from services.queue import enqueue_render_job as _enqueue  # type: ignore
    enqueue_render_job = _enqueue
    logger.info("Loaded services.queue.enqueue_render_job")
except Exception:
    logger.warning("services.queue.enqueue_render_job not found — background enqueue disabled")

# ---------- Job file helpers ----------
def job_file_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"

def read_job(job_id: str) -> Optional[Dict[str, Any]]:
    p = job_file_path(job_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to read job file %s", p)
        return None

def write_job(job: Dict[str, Any]):
    p = job_file_path(job["id"])
    try:
        p.write_text(json.dumps(job, ensure_ascii=False, default=str), encoding="utf-8")
    except Exception:
        logger.exception("Failed to write job file %s", p)

def update_job_status(job_id: str, status: str, progress: Optional[int] = None, extra: Dict[str, Any] = None):
    job = read_job(job_id) or {"id": job_id}
    job["status"] = status
    if progress is not None:
        job["progress"] = int(progress)
    job.setdefault("meta", {})
    job["meta"]["last_update_at"] = datetime.now(timezone.utc).isoformat()
    if extra:
        job.setdefault("meta", {}).update(extra)
    if status in ("completed", "failed"):
        job["completed_at"] = datetime.now(timezone.utc).isoformat()
    write_job(job)
    logger.info("Job %s status -> %s (progress=%s)", job_id, status, job.get("progress"))

def set_job_result(job_id: str, result: Dict[str, Any]):
    job = read_job(job_id) or {"id": job_id}
    job["result"] = result
    job["status"] = "completed"
    job["completed_at"] = datetime.now(timezone.utc).isoformat()
    write_job(job)
    logger.info("Job %s completed, result set", job_id)

def set_job_failed(job_id: str, error_msg: str):
    job = read_job(job_id) or {"id": job_id}
    job["status"] = "failed"
    job["error"] = error_msg
    job["completed_at"] = datetime.now(timezone.utc).isoformat()
    write_job(job)
    logger.error("Job %s failed: %s", job_id, error_msg)

# ---------- Utility: stable job response ----------
def job_response(job_id: str) -> Dict[str, Any]:
    job = read_job(job_id)
    if not job:
        return {"id": job_id, "status": "not_found", "progress": 0, "video_url": None}
    # progress default 0..100
    progress = int(job.get("progress", 0))
    video_url = None
    # If job has explicit result.video_url, use it
    result = job.get("result") or {}
    if isinstance(result, dict):
        video_url = result.get("video_url")
    # If not, attempt local public path
    if not video_url:
        out_local = OUTPUT_DIR / f"{job_id}.mp4"
        if out_local.exists():
            # If BASE_URL configured, expose stable path
            if BASE_URL:
                video_url = f"{BASE_URL}/videos/{job_id}.mp4"
            else:
                # fallback: server direct url path
                video_url = f"/videos/{job_id}.mp4"
    return {"id": job_id, "status": job.get("status", "created"), "progress": progress, "video_url": video_url}

# ---------- Optional: S3 upload helper ----------
def upload_to_s3_if_configured(local_path: str, s3_key: str) -> Optional[str]:
    if not (S3_BUCKET and AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY and AWS_REGION):
        return None
    try:
        import boto3
        s3 = boto3.client("s3",
                          aws_access_key_id=AWS_ACCESS_KEY_ID,
                          aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
                          region_name=AWS_REGION)
        s3.upload_file(local_path, S3_BUCKET, s3_key)
        url = f"https://{S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{s3_key}"
        logger.info("Uploaded %s -> s3://%s/%s", local_path, S3_BUCKET, s3_key)
        return url
    except Exception:
        logger.exception("S3 upload failed")
        return None

# ---------- ROUTES ----------
@app.route("/health", methods=["GET"])
def health():
    # lightweight health check (always ok even if workers busy)
    return jsonify({"status": "ok", "time": datetime.now(timezone.utc).isoformat()})

@app.route("/create-video", methods=["POST"])
def create_video():
    """
    Create job endpoint: lightweight parsing only.
    Returns immediately: {"ok": True, "job_id": "...", "status":"created", "warning": "..."}
    """
    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            return jsonify({"error": "invalid_json", "message": "Invalid JSON payload"}), 400

        script_text = data.get("script_text") or data.get("script") or ""
        preset = data.get("preset", "short")
        avatar = data.get("avatar")  # optional avatar reference
        meta = data.get("meta", {})
        render_settings = data.get("render_settings", {})

        if not script_text or not script_text.strip():
            return jsonify({"error": "missing_script", "message": "script_text is required"}), 400

        if preset not in ALLOWED_PRESETS:
            preset = "short"

        jid = str(uuid.uuid4())
        job = {
            "id": jid,
            "script_text": script_text,
            "preset": preset,
            "avatar": avatar,
            "meta": meta,
            "render_settings": render_settings,
            "status": "created",
            "progress": 0,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        write_job(job)
        logger.info("Created job %s preset=%s", jid, preset)

        warning = None
        enqueue_ok = False
        if enqueue_render_job is not None:
            try:
                enqueue_render_job(jid)  # attempt immediate enqueue
                enqueue_ok = True
                update_job_status(jid, "queued", progress=1)
                logger.info("Enqueued job %s to background worker", jid)
            except Exception:
                logger.exception("enqueue_render_job failed for %s", jid)
                warning = "enqueue_failed"
        else:
            warning = "enqueue_disabled"

        resp = {"ok": True, "job_id": jid, "status": job["status"]}
        if warning:
            resp["warning"] = warning
        # return 201
        return jsonify(resp), 201

    except Exception as e:
        logger.exception("create_video failed")
        return jsonify({"error": "create_failed", "message": str(e)}), 500

@app.route("/render/start/<job_id>", methods=["GET", "POST"])
def start_render(job_id: str):
    """
    Manual start (admin optional). Marks job queued and enqueues celery task.
    Prevents duplicate starts.
    """
    # optional admin protection
    if ADMIN_API_KEY:
        key = request.headers.get("x-api-key") or request.args.get("api_key")
        if key != ADMIN_API_KEY:
            return jsonify({"error": "unauthorized", "message": "Invalid admin api key"}), 401

    job = read_job(job_id)
    if not job:
        return jsonify({"error": "job_not_found", "message": "Job not found"}), 404

    if job.get("status") in ("started", "queued", "processing"):
        return jsonify({"error": "already_started", "message": "Job already started or queued."}), 400

    job["status"] = "queued"
    job.setdefault("meta", {})
    job["meta"]["manually_started_at"] = datetime.now(timezone.utc).isoformat()
    write_job(job)

    if enqueue_render_job is None:
        logger.warning("enqueue_render_job not configured; cannot queue job %s", job_id)
        return jsonify({"ok": True, "job_id": job_id, "status": "queued", "warning": "enqueue_disabled"}), 200

    try:
        enqueue_render_job(job_id)
        update_job_status(job_id, "queued", progress=1)
        return jsonify({"ok": True, "job_id": job_id, "status": "queued"}), 200
    except Exception:
        logger.exception("Failed to enqueue job %s", job_id)
        update_job_status(job_id, "created", extra={"enqueue_error": True})
        return jsonify({"error": "enqueue_failed", "message": "Failed to add to queue"}), 500

@app.route("/job/<job_id>", methods=["GET"])
def job_status(job_id: str):
    """
    Return stable structure:
    {id: ..., status: ..., progress: number, video_url: "..." }
    """
    try:
        resp = job_response(job_id)
        return jsonify(resp)
    except Exception:
        logger.exception("job_status error for %s", job_id)
        return jsonify({"error": "internal", "message": "failed to fetch job status"}), 500

@app.route("/download/<job_id>", methods=["GET"])
def download(job_id: str):
    """
    Serve final mp4 if exists locally, else try job.result.video_url,
    else structured error.
    """
    try:
        job = read_job(job_id)
        if not job:
            return jsonify({"error": "job_not_found", "message": "Job not found"}), 404

        # prefer explicit result url (S3 or absolute)
        result = job.get("result", {}) or {}
        if isinstance(result, dict) and result.get("video_url"):
            # If video_url is an absolute URL, return JSON with that URL
            url = result.get("video_url")
            if url.startswith("http://") or url.startswith("https://"):
                return jsonify({"video_url": url})
            # else, if it's a relative path under public, try to serve
            candidate = Path(url.lstrip("/"))
            if candidate.exists():
                return send_file(str(candidate), as_attachment=True)
            # fallthrough to local file check

        final_local = OUTPUT_DIR / f"{job_id}.mp4"
        if final_local.exists():
            # Serve file directly
            return send_file(str(final_local), as_attachment=True)

        # fallback: check public folder path
        static_path = BASE_DIR / "public" / "videos" / f"{job_id}.mp4"
        if static_path.exists():
            return send_file(str(static_path), as_attachment=True)

        # if job is still processing
        if job.get("status") not in ("completed", "failed"):
            return jsonify({"error": "processing", "message": "File is still processing", "status": job.get("status")}), 202

        # final not found
        return jsonify({"error": "not_found", "message": "Output file not found"}), 404

    except Exception:
        logger.exception("download error for %s", job_id)
        return jsonify({"error": "internal", "message": "download failed"}), 500

# ---------- Admin endpoints ----------
def require_admin():
    if not ADMIN_API_KEY:
        abort(403, description="Admin API key not configured")
    key = request.headers.get("x-api-key") or request.args.get("api_key")
    if key != ADMIN_API_KEY:
        abort(401, description="Unauthorized")

@app.route("/admin/jobs", methods=["GET"])
def admin_list_jobs():
    try:
        require_admin()
        limit = int(request.args.get("limit", 200))
        skip = int(request.args.get("skip", 0))
        files = sorted(JOBS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        items = []
        for p in files[skip:skip+limit]:
            try:
                items.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                continue
        return jsonify({"count": len(items), "jobs": items})
    except Exception:
        logger.exception("admin_list_jobs failed")
        return jsonify({"error": "internal"}), 500

@app.route("/admin/clear-failed", methods=["POST"])
def admin_clear_failed():
    try:
        require_admin()
        removed = []
        for p in JOBS_DIR.glob("*.json"):
            try:
                j = json.loads(p.read_text(encoding="utf-8"))
                if j.get("status") == "failed":
                    # delete job json and out file
                    out = OUTPUT_DIR / f"{j['id']}.mp4"
                    try:
                        p.unlink()
                    except Exception:
                        pass
                    if out.exists():
                        try:
                            out.unlink()
                        except Exception:
                            pass
                    removed.append(j["id"])
            except Exception:
                continue
        return jsonify({"ok": True, "removed": removed})
    except Exception:
        logger.exception("admin_clear_failed failed")
        return jsonify({"error": "internal"}), 500

@app.route("/admin/queue", methods=["GET"])
def admin_queue_info():
    try:
        require_admin()
        if enqueue_render_job is None:
            return jsonify({"error": "queue_not_configured"}), 400
        try:
            # if services.celery_app.control.inspect available, return info
            from services.celery_app import celery_app  # type: ignore
            insp = celery_app.control.inspect()
            return jsonify({
                "active": insp.active() or {},
                "reserved": insp.reserved() or {},
                "scheduled": insp.scheduled() or {},
                "registered": insp.registered() or {},
            })
        except Exception:
            logger.exception("Queue inspect failed")
            return jsonify({"error": "inspect_failed"}), 500
    except Exception:
        logger.exception("admin_queue_info failed")
        return jsonify({"error": "internal"}), 500

@app.route("/admin/workers", methods=["GET"])
def admin_workers():
    try:
        require_admin()
        from services.celery_app import celery_app  # type: ignore
        insp = celery_app.control.inspect()
        return jsonify({"registered": insp.registered() or {}, "active": insp.active() or {}})
    except Exception:
        logger.exception("admin_workers failed")
        return jsonify({"error": "internal"}), 500

# ---------- Cleanup job - utility route (admin) ----------
@app.route("/admin/cleanup/<job_id>", methods=["POST"])
def admin_cleanup_job(job_id: str):
    try:
        require_admin()
        job_file = JOBS_DIR / f"{job_id}.json"
        out_file = OUTPUT_DIR / f"{job_id}.mp4"
        try:
            if job_file.exists():
                job_file.unlink()
            if out_file.exists():
                out_file.unlink()
            return jsonify({"ok": True})
        except Exception as e:
            logger.exception("cleanup failed")
            return jsonify({"error": "cleanup_failed", "message": str(e)}), 500
    except Exception:
        logger.exception("admin_cleanup_job failed")
        return jsonify({"error": "internal"}), 500

# ---------- Error handlers (structured JSON) ----------
@app.errorhandler(400)
def bad_request(e):
    return jsonify({"error": "bad_request", "message": str(e)}), 400

@app.errorhandler(401)
def unauthorized(e):
    return jsonify({"error": "unauthorized", "message": str(e)}), 401

@app.errorhandler(403)
def forbidden(e):
    return jsonify({"error": "forbidden", "message": str(e)}), 403

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "not_found", "message": str(e)}), 404

@app.errorhandler(500)
def internal_error(e):
    # Log traceback
    tb = traceback.format_exc()
    logger.error("Unhandled exception: %s\n%s", e, tb)
    return jsonify({"error": "internal", "message": "Internal server error"}), 500

# ---------- Run ----------
if __name__ == "__main__":
    # For development only - use gunicorn in production with long timeout settings
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=(os.environ.get("FLASK_DEBUG") == "1"))
