#!/usr/bin/env python3
"""
app.py - VISORA backend entrypoint
Features:
- /generate-video        (sync)  : Accepts "script" -> runs Scene Engine -> runs 3D generator -> uploads to S3 -> returns video metadata
- /generate-video-async  (async) : Enqueue job to Redis (RQ) if available; returns job id
- /job-status/<job_id>   : Check async job status
- /download/<video_id>   : Download stored file (mostly for local testing)
- Health endpoint /
Notes:
- Requires engine.cinematic_engine and engine.generator_3d to be present.
- Requires AWS credentials set in environment for S3 upload.
"""

import os
import uuid
import json
import logging
import traceback
from datetime import datetime
from flask import Flask, request, jsonify, send_file, abort
from werkzeug.utils import secure_filename

# Scene engine and 3D generator (must exist)
from engine.cinematic_engine import CinematicSceneEngine

# generator_3d should expose generate_scene_video(scenes, output_path, options={})
# implement the heavy GPU inference inside that module to keep app.py clean.
try:
    from engine.generator_3d import generate_scene_video
    GENERATOR_AVAILABLE = True
except Exception:
    # generator_3d may not be ready locally; keep flag to handle gracefully
    GENERATOR_AVAILABLE = False

# Optional: Redis RQ for async jobs (if installed and configured)
USE_RQ = False
try:
    import redis
    from rq import Queue, get_current_job
    USE_RQ = True
except Exception:
    USE_RQ = False

# S3 client
import boto3
from botocore.exceptions import ClientError

# Config from env (assume user already set these in Render/AWS)
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
S3_BUCKET = os.getenv("S3_BUCKET", "")
S3_KEY_PREFIX = os.getenv("S3_KEY_PREFIX", "videos/")
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "outputs")
WORK_DIR = os.getenv("WORK_DIR", "/tmp/visora_work")

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(WORK_DIR, exist_ok=True)

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("visora-app")

# Initialize Flask
app = Flask(__name__)

# Initialize engines
scene_engine = CinematicSceneEngine()

# Init S3 client (uses env AWS_ACCESS_KEY_ID & AWS_SECRET_ACCESS_KEY)
s3_client = boto3.client("s3", region_name=AWS_REGION)

# RQ queue if available and REDIS_URL env present
rq_queue = None
if USE_RQ:
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
    try:
        redis_conn = redis.from_url(REDIS_URL)
        rq_queue = Queue("visora_video_queue", connection=redis_conn)
        logger.info("RQ queue configured.")
    except Exception as e:
        logger.warning("RQ configured but connection failed: %s", str(e))
        rq_queue = None

# Helper utilities
def make_video_id():
    return uuid.uuid4().hex

def local_video_path(video_id):
    # safe filename
    filename = secure_filename(f"{video_id}.mp4")
    return os.path.join(OUTPUT_DIR, filename)

def s3_key_for(video_id):
    # ensure prefix ends with /
    prefix = S3_KEY_PREFIX or ""
    if prefix and not prefix.endswith("/"):
        prefix = prefix + "/"
    return f"{prefix}{video_id}.mp4"

def upload_to_s3(local_path, key):
    try:
        s3_client.upload_file(local_path, S3_BUCKET, key)
        s3_url = f"s3://{S3_BUCKET}/{key}"
        # also return https URL (public access depends on bucket policy; we assume presigned used)
        presigned = s3_client.generate_presigned_url('get_object', Params={'Bucket': S3_BUCKET, 'Key': key}, ExpiresIn=3600)
        return {"s3_url": s3_url, "presigned_url": presigned}
    except ClientError as e:
        logger.error("S3 upload failed: %s", str(e))
        raise

def safe_write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def run_generation_pipeline(script_text, video_id=None, options=None):
    """
    Runs the full pipeline synchronously:
    - scene extraction
    - call 3D generator (local/AWS GPU)
    - upload file to S3
    Returns metadata dict
    """
    options = options or {}
    video_id = video_id or make_video_id()
    vid_path = local_video_path(video_id)
    meta = {
        "video_id": video_id,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "script": script_text,
        "status": "started",
        "scenes": None,
        "local_path": vid_path,
        "s3": None,
        "error": None,
    }

    logger.info("Starting pipeline for video_id=%s", video_id)
    try:
        # 1) Scene engine
        scenes = scene_engine.extract(script_text)
        meta["scenes"] = scenes
        logger.info("Scenes generated for %s: %s", video_id, scenes)

        # Save scenes json for debugging
        scenes_path = os.path.join(WORK_DIR, f"{video_id}_scenes.json")
        safe_write_json(scenes_path, scenes)

        # 2) 3D generator (heavy lifting) - MUST be implemented in engine.generator_3d
        if not GENERATOR_AVAILABLE:
            raise RuntimeError("3D generator module not available. Implement engine.generator_3d.generate_scene_video.")

        # generator should write final mp4 at vid_path
        generate_scene_video(scenes, vid_path, options=options)
        if not os.path.exists(vid_path) or os.path.getsize(vid_path) == 0:
            raise RuntimeError(f"Generator produced no output (empty file) at {vid_path}")

        meta["status"] = "generated"
        logger.info("Video generated locally at %s", vid_path)

        # 3) Upload to S3 (optional, if S3 configured)
        if S3_BUCKET:
            key = s3_key_for(video_id)
            s3_info = upload_to_s3(vid_path, key)
            meta["s3"] = s3_info
            meta["status"] = "uploaded"
            logger.info("Uploaded %s to S3 as %s", vid_path, key)
        else:
            logger.info("S3_BUCKET not configured; skipping upload.")

        # save metadata
        meta_path = os.path.join(WORK_DIR, f"{video_id}_meta.json")
        safe_write_json(meta_path, meta)
        return meta

    except Exception as e:
        logger.error("Pipeline failed for %s: %s", video_id, str(e))
        logger.debug(traceback.format_exc())
        meta["status"] = "error"
        meta["error"] = str(e)
        # attempt to persist error metadata
        try:
            meta_path = os.path.join(WORK_DIR, f"{video_id}_meta.json")
            safe_write_json(meta_path, meta)
        except Exception:
            pass
        return meta

# If RQ available, create an RQ job function wrapper
if USE_RQ and rq_queue:
    def rq_job_generate(script_text, options=None):
        job = get_current_job()
        vid = make_video_id()
        # Update job meta
        job.meta.update({"video_id": vid, "status": "queued", "script_preview": script_text[:120]})
        job.save_meta()
        # Run pipeline
        result = run_generation_pipeline(script_text, video_id=vid, options=options)
        job.meta.update({"status": result.get("status"), "s3": result.get("s3")})
        job.save_meta()
        return result

# Routes

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "VISORA_ENGINE_ACTIVE", "generator_available": GENERATOR_AVAILABLE, "rq_enabled": bool(rq_queue)})

@app.route("/generate-video", methods=["POST"])
def generate_video_endpoint():
    """
    Synchronous generation endpoint (blocking).
    JSON body: {"script": "...", "options": {...}}
    Response: metadata JSON
    """
    try:
        data = request.get_json(force=True)
        script = data.get("script", "")
        options = data.get("options", {})

        if not script or len(script.strip()) < 2:
            return jsonify({"error": "script missing or too short"}), 400

        # Generate unique video id
        video_id = make_video_id()

        # Run pipeline (blocking) - this will call generator_3d
        meta = run_generation_pipeline(script, video_id=video_id, options=options)
        status_code = 200 if meta.get("status") in ("uploaded", "generated") else 500
        return jsonify(meta), status_code

    except Exception as e:
        logger.error("generate-video endpoint error: %s", str(e))
        return jsonify({"error": str(e)}), 500

@app.route("/generate-video-async", methods=["POST"])
def generate_video_async():
    """
    Enqueue job to Redis RQ if available.
    Returns job id immediately.
    """
    if not rq_queue:
        return jsonify({"error": "Async queue not configured (REDIS_URL missing or rq not installed)"}), 400

    try:
        data = request.get_json(force=True)
        script = data.get("script", "")
        options = data.get("options", {})

        if not script or len(script.strip()) < 2:
            return jsonify({"error": "script missing or too short"}), 400

        # enqueue job
        job = rq_queue.enqueue(rq_job_generate, script, options)
        return jsonify({"ok": True, "job_id": job.get_id(), "enqueue_time": str(datetime.utcnow())}), 200
    except Exception as e:
        logger.error("generate-video-async error: %s", str(e))
        return jsonify({"error": str(e)}), 500

@app.route("/job-status/<job_id>", methods=["GET"])
def job_status(job_id):
    if not rq_queue:
        return jsonify({"error": "Async queue not configured"}), 400
    try:
        from rq.job import Job
        job = Job.fetch(job_id, connection=rq_queue.connection)
        meta = {
            "id": job.get_id(),
            "status": job.get_status(),
            "result": job.result,
            "meta": job.meta
        }
        return jsonify(meta), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/download/<video_id>", methods=["GET"])
def download_video(video_id):
    # local file
    path = local_video_path(video_id)
    if os.path.exists(path):
        return send_file(path, as_attachment=True, download_name=f"{video_id}.mp4")
    else:
        return jsonify({"error": "file not found"}), 404

# simple endpoint to list outputs (for debugging only)
@app.route("/list-outputs", methods=["GET"])
def list_outputs():
    files = []
    for f in os.listdir(OUTPUT_DIR):
        if f.endswith(".mp4"):
            full = os.path.join(OUTPUT_DIR, f)
            files.append({"file": f, "size": os.path.getsize(full)})
    return jsonify({"outputs": files})

# Run
if __name__ == "__main__":
    # production: run with gunicorn in Render/EC2, this is fallback dev server
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
