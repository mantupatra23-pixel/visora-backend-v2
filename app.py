"""
app.py - Production-ready Visora backend main app (Flask)

Features:
- POST /create-video  -> create job from JSON (script, preset, avatar, webhook, etc)
- GET  /render/start/<job_id> -> manual start (enqueue)
- GET  /job/<job_id>  -> job status + meta
- GET  /download/<job_id> -> serve final mp4 if present (or return S3 URL)
- GET  /health -> quick health check

Admin:
- GET /admin/jobs -> list jobs (protected)
- GET /admin/queue -> celery queue info (protected)
- GET /admin/workers -> celery worker stats (protected)

Production notes:
- Set env variables (see README / comments below)
- Start Celery workers separately
- Use Gunicorn (example below) for production
"""

import os
import json
import uuid
import shutil
import logging
import traceback
from pathlib import Path
from datetime import datetime
from typing import Optional
from flask import Flask, request, jsonify, send_file, send_from_directory, abort
from flask_cors import CORS
import requests

# ---- CONFIG (ENV) ----
BASE_DIR = Path(__file__).resolve().parent
JOBS_DIR = BASE_DIR / "jobs"
OUTPUT_DIR = BASE_DIR / "public" / "videos"
JOBS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Public URL of this backend (important for returned video URLs)
BASE_URL = os.environ.get("BASE_URL", "https://visora-backend-v2.onrender.com")
# Optional Bacon service webhook URL
BACON_URL = os.environ.get("BACON_URL", "")
# Admin API key to protect admin routes / manual start (set strong value in env)
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "CHANGE_ME_API_KEY")

# Storage / TTS / external keys
S3_BUCKET = os.environ.get("S3_BUCKET", "")
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")

# Allowed frontend origins for CORS (comma separated)
CORS_ORIGINS = os.environ.get("CORS_ORIGINS", BASE_URL)
# Presets
ALLOWED_PRESETS = ["reel", "short", "cinematic"]

# ---- Logging ----
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("visora_app")

# ---- Try to import repository Job model (if exists) ----
Job = None
try:
    # if your repo has models.Job, it will be used. adapt import path if different.
    from models import Job as RepoJob
    Job = RepoJob
    logger.info("Using repository Job model")
except Exception:
    logger.info("Repository Job model not found - using local file-based Job model fallback")


# ---- Try to import enqueue function (Celery) ----
enqueue_render_job = None
try:
    from services.queue import enqueue_render_job as _enqueue
    enqueue_render_job = _enqueue
    logger.info("Loaded services.queue.enqueue_render_job")
except Exception:
    logger.warning("services.queue.enqueue_render_job not found - enqueue will be unavailable")


# ---- S3 helper (optional) ----
def upload_to_s3_if_configured(local_path: str, key: str) -> Optional[str]:
    """
    Uploads to S3 if S3_BUCKET + AWS credentials are set.
    Returns public URL or None.
    """
    if not (S3_BUCKET and AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY):
        return None
    try:
        import boto3
        s3 = boto3.client(
            "s3",
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=AWS_REGION
        )
        s3.upload_file(local_path, S3_BUCKET, key, ExtraArgs={"ACL": "public-read", "ContentType": "video/mp4"})
        url = f"https://{S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{key}"
        logger.info("Uploaded %s -> %s", local_path, url)
        return url
    except Exception:
        logger.exception("S3 upload failed")
        return None


# ---- Local fallback Job model ----
if Job is None:
    class Job:
        storage_dir = JOBS_DIR

        def __init__(self, id, script_text="", preset="short", avatar=None, status="created",
                     meta=None, result=None, created_at=None, completed_at=None, render_settings=None):
            self.id = id
            self.script_text = script_text
            self.preset = preset
            self.avatar_reference_path = avatar
            self.status = status
            self.meta = meta or {}
            self.result = result or {}
            self.created_at = created_at or datetime.utcnow().isoformat()
            self.completed_at = completed_at
            self.error = None
            self.render_settings = render_settings or {}

        @property
        def path(self):
            return self.storage_dir / f"{self.id}.json"

        def to_dict(self):
            return {
                "id": self.id,
                "script_text": self.script_text,
                "preset": self.preset,
                "avatar_reference_path": self.avatar_reference_path,
                "status": self.status,
                "meta": self.meta,
                "result": self.result,
                "created_at": self.created_at,
                "completed_at": self.completed_at,
                "error": self.error,
                "render_settings": self.render_settings,
            }

        def save(self):
            try:
                with open(self.path, "w", encoding="utf-8") as f:
                    json.dump(self.to_dict(), f, indent=2)
            except Exception:
                logger.exception("Failed saving job %s", self.id)
                raise

        @classmethod
        def get(cls, id):
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
                    avatar=data.get("avatar_reference_path"),
                    status=data.get("status", "created"),
                    meta=data.get("meta", {}),
                    result=data.get("result", {}),
                    created_at=data.get("created_at"),
                    completed_at=data.get("completed_at"),
                    render_settings=data.get("render_settings", {}),
                )
                job.error = data.get("error")
                return job
            except Exception:
                logger.exception("Failed reading job file %s", p)
                return None

        @classmethod
        def create(cls, script_text, preset="short", avatar=None, meta=None, render_settings=None):
            jid = str(uuid.uuid4())
            job = Job(id=jid, script_text=script_text, preset=preset, avatar=avatar, meta=meta or {}, render_settings=render_settings or {})
            job.status = "created"
            job.save()
            return job

        @classmethod
        def find_many(cls, limit=100, skip=0):
            files = sorted(cls.storage_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            out = []
            for p in files[skip: skip + limit]:
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    out.append(data)
                except Exception:
                    continue
            return out


# ---- Flask app init ----
app = Flask(__name__, static_folder=str(BASE_DIR / "public"))
# Enable CORS for configured origins
origins = [o.strip() for o in (CORS_ORIGINS or "").split(",") if o.strip()]
if not origins:
    origins = ["*"]
CORS(app, resources={r"/*": {"origins": origins}})
logger.info("CORS enabled for origins: %s", origins)


# ---- util: admin auth ----
def require_admin():
    key = request.headers.get("x-api-key") or request.args.get("api_key")
    if key != ADMIN_API_KEY:
        abort(401, description="Unauthorized")


# ---- ROUTES ----

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "ok": True,
        "time": datetime.utcnow().isoformat(),
        "jobs_dir": str(JOBS_DIR),
        "output_dir": str(OUTPUT_DIR),
        "base_url": BASE_URL
    })


@app.route("/create-video", methods=["POST"])
def create_video():
    """
    Create a new job.
    JSON:
      { script_text, preset (optional), avatar (optional), meta (optional), render_settings (optional) }
    """
    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            return jsonify({"error": "Invalid JSON"}), 400

        script_text = data.get("script_text") or data.get("script") or ""
        preset = data.get("preset", "short")
        avatar = data.get("avatar")
        meta = data.get("meta", {})
        render_settings = data.get("render_settings", {})

        if not script_text:
            return jsonify({"error": "script_text required"}), 400
        if preset not in ALLOWED_PRESETS:
            preset = "short"

        job = Job.create(script_text=script_text, preset=preset, avatar=avatar, meta=meta, render_settings=render_settings)
        logger.info("Created job %s preset=%s", job.id, job.preset)
        return jsonify({"ok": True, "job_id": job.id, "status": job.status}), 201
    except Exception as e:
        logger.exception("create_video failed")
        return jsonify({"error": str(e)}), 500


@app.route("/render/start/<job_id>", methods=["GET"])
def start_render(job_id):
    """
    Manual start: protects by ADMIN_API_KEY if set.
    Marks job queued and enqueues to Celery if configured.
    """
    try:
        # require_admin()  # uncomment to protect with ADMIN_API_KEY

        job = Job.get(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404

        if job.status in ("started", "parsing", "rendering", "completed"):
            return jsonify({"error": f"Job already {job.status}"}), 409

        job.status = "queued"
        job.meta = job.meta or {}
        job.meta["manual_started"] = True
        job.meta["manual_started_at"] = datetime.utcnow().isoformat()
        # Save using available save method
        try:
            job.save()
        except Exception:
            try:
                job.save_sync()
            except Exception:
                pass

        if enqueue_render_job is None:
            logger.error("enqueue_render_job not configured, cannot enqueue %s", job_id)
            return jsonify({"error": "enqueue function not configured"}), 500

        enqueue_render_job(str(job.id))
        logger.info("Enqueued job %s", job.id)
        return jsonify({"ok": True, "job_id": str(job.id), "status": job.status}), 200

    except Exception as e:
        logger.exception("start_render error")
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/job/<job_id>", methods=["GET"])
def job_status(job_id):
    job = Job.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({"job": job.to_dict()})


@app.route("/download/<job_id>", methods=["GET"])
def download(job_id):
    """
    Serve final mp4 if exists locally. Otherwise returns job.result.video_url if present.
    NOTE: This endpoint will serve the binary file (as attachment by default).
    """
    try:
        job = Job.get(job_id)
        # prefer explicit result.video_url (S3 or otherwise)
        if job and job.result and job.result.get("video_url"):
            # If result is S3 or absolute URL, return it
            url = job.result.get("video_url")
            return jsonify({"video_url": url})

        final_local = OUTPUT_DIR / f"{job_id}.mp4"
        if final_local.exists():
            # Serve file directly
            return send_file(str(final_local), mimetype="video/mp4", as_attachment=True, download_name=f"{job_id}.mp4")

        # fallback: if file exists under public static path, try to serve
        static_path = BASE_DIR / "public" / "videos" / f"{job_id}.mp4"
        if static_path.exists():
            return send_file(str(static_path), mimetype="video/mp4", as_attachment=True, download_name=f"{job_id}.mp4")

        # not found
        public_fallback = f"{BASE_URL}/download/{job_id}"
        return jsonify({"error": "Output not found", "expected_url": public_fallback}), 404

    except Exception:
        logger.exception("download error")
        return jsonify({"error": "internal"}), 500


# Admin endpoints (protected)
@app.route("/admin/jobs", methods=["GET"])
def admin_list_jobs():
    require_admin()
    items = Job.find_many(limit=200, skip=0)
    return jsonify({"count": len(items), "jobs": items})


@app.route("/admin/queue", methods=["GET"])
def admin_queue_info():
    require_admin()
    try:
        from services.celery_app import celery_app
        insp = celery_app.control.inspect()
        return jsonify({
            "active": insp.active() or {},
            "reserved": insp.reserved() or {},
            "scheduled": insp.scheduled() or {},
            "registered": insp.registered() or {},
        })
    except Exception as e:
        logger.exception("Queue inspect failed")
        return jsonify({"error": str(e)}), 500


@app.route("/admin/workers", methods=["GET"])
def admin_workers():
    require_admin()
    try:
        from services.celery_app import celery_app
        insp = celery_app.control.inspect()
        return jsonify({"workers": insp.stats() or {}})
    except Exception as e:
        logger.exception("Worker inspect failed")
        return jsonify({"error": str(e)}), 500


# DEV helper: cleanup
@app.route("/_internal/cleanup_job/<job_id>", methods=["POST"])
def _cleanup_job(job_id):
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
        return jsonify({"error": str(e)}), 500


# Utility to be called by render_task when job completes successfully
def finalize_job_success(job_id: str, local_out_path: str):
    """
    Called by render worker when final mp4 produced locally.
    Uploads to S3 if configured, updates job.result, fires Bacon webhook if configured.
    """
    try:
        job = Job.get(job_id)
        if not job:
            logger.error("finalize_job_success: job not found %s", job_id)
            return False

        # Try upload to S3
        s3_key = f"videos/{job_id}.mp4"
        s3_url = upload_to_s3_if_configured(local_out_path, s3_key)

        if s3_url:
            job.result = {"video_url": s3_url, "path": local_out_path}
        else:
            # Expose via backend public endpoint
            public_url = f"{BASE_URL}/download/{job_id}"
            job.result = {"video_url": public_url, "path": local_out_path}

        job.status = "completed"
        job.completed_at = datetime.utcnow().isoformat()
        try:
            job.save()
        except Exception:
            try:
                job.save_sync()
            except Exception:
                pass

        # Fire Bacon webhook if configured (or job.meta.webhook_url)
        webhook = job.meta.get("webhook_url") if job.meta else None
        if not webhook and BACON_URL:
            webhook = BACON_URL

        if webhook:
            try:
                requests.post(webhook, json={"status": "completed", "job_id": job_id, "video_url": job.result.get("video_url")}, timeout=8)
                logger.info("Fired webhook %s for job %s", webhook, job_id)
            except Exception:
                logger.exception("Webhook call failed for job %s", job_id)

        return True
    except Exception:
        logger.exception("finalize_job_success failed for %s", job_id)
        return False


# Utility to mark job failed
def finalize_job_failed(job_id: str, error_msg: str):
    try:
        job = Job.get(job_id)
        if not job:
            logger.error("finalize_job_failed: job not found %s", job_id)
            return False
        job.status = "failed"
        job.error = error_msg
        try:
            job.save()
        except Exception:
            try:
                job.save_sync()
            except Exception:
                pass

        # optionally fire webhook for failure
        webhook = job.meta.get("webhook_url") if job.meta else None
        if not webhook and BACON_URL:
            webhook = BACON_URL
        if webhook:
            try:
                requests.post(webhook, json={"status": "failed", "job_id": job_id, "error": error_msg}, timeout=8)
            except Exception:
                logger.exception("Failed to call webhook on job failure")
        return True
    except Exception:
        logger.exception("finalize_job_failed error")
        return False


# ---- Run app (WSGI compatible) ----
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("DEBUG", "0") == "1"
    logger.info("Starting Visora backend on port %s (debug=%s)", port, debug)
    app.run(host="0.0.0.0", port=port, debug=debug)
