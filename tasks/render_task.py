"""
tasks/render_task.py - Celery render worker (file-based job store).
This task reads jobs/{job_id}.json, runs pipeline (TTS -> lipsync/blender -> combine),
writes final file to public/videos/{job_id}.mp4 and marks job status.
You must replace the placeholder TTS/lipsync/render calls with your real engines.
"""

import os
import json
import logging
import traceback
from pathlib import Path
from datetime import datetime
from celery import Celery

# CONFIG (env)
REDIS_URL = os.environ.get("REDIS_URL", "rediss://default:AUNYAAIncDI0YjRjYjMyMjVmYzI0Yjk2ODgwZTk4NzZjZjYxYjZkY3AyMTcyNDA@included-civet-17240.upstash.io:6379")
JOBS_DIR = Path(os.environ.get("JOBS_DIR", "./jobs"))
OUTPUT_DIR = Path(os.environ.get("VIDEO_SAVE_DIR", "./public/videos"))
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")

JOBS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Celery app
celery_app = Celery("visora_render", broker=REDIS_URL, backend=REDIS_URL)

LOG = logging.getLogger("tasks.render_task")
LOG.setLevel(logging.INFO)

# helpers
def job_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"

def read_job(job_id: str) -> dict:
    p = job_path(job_id)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))

def write_job(job: dict):
    p = job_path(job["id"])
    p.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")

def update_status(job_id: str, status: str, progress: int = None, extra=None):
    job = read_job(job_id) or {"id": job_id}
    job["status"] = status
    if progress is not None:
        job["progress"] = int(max(0, min(100, progress)))
    job.setdefault("meta", {}).update(extra or {})
    job["meta"]["last_update_at"] = datetime.utcnow().isoformat() + "Z"
    write_job(job)
    LOG.info("Job %s status=%s progress=%s", job_id, status, job.get("progress"))

def set_result_video(job_id: str, local_path: str):
    job = read_job(job_id) or {"id": job_id}
    public_url = f"{BASE_URL}/videos/{job_id}.mp4" if BASE_URL else f"/videos/{job_id}.mp4"
    job["result"] = {"video_url": public_url}
    job["status"] = "completed"
    job["progress"] = 100
    job["completed_at"] = datetime.utcnow().isoformat() + "Z"
    write_job(job)
    LOG.info("Job %s finished, video %s", job_id, public_url)

# Example pipeline functions (replace with real integration)
def do_tts(job, out_audio_path: Path) -> bool:
    # placeholder: create a tiny wav/mp3 to avoid failures
    try:
        text = job.get("script_text","")
        # create 1 second silent file as placeholder using python wave or ffmpeg if available
        out_audio_path.write_bytes(b"")  # empty file fallback
        return True
    except Exception:
        LOG.exception("tts failed")
        return False

def do_lipsync(job, audio_path: Path, out_video_path: Path) -> bool:
    # placeholder: copy a sample short file or create empty mp4
    try:
        out_video_path.write_bytes(b"")
        return True
    except Exception:
        LOG.exception("lipsync failed")
        return False

def combine_assets(job, video_path: Path, final_path: Path) -> bool:
    try:
        # move/copy
        video_path.replace(final_path)
        return True
    except Exception:
        LOG.exception("combine failed")
        return False

# Celery task
@celery_app.task(bind=True, name="tasks.render_task.render_job_task")
def render_job_task(self, job_id: str):
    LOG.info("render_job_task start: %s", job_id)
    job = read_job(job_id)
    if not job:
        LOG.error("Job file missing %s", job_id)
        raise FileNotFoundError(job_id)

    try:
        update_status(job_id, "started", progress=1)
        # 1) TTS
        update_status(job_id, "tts", progress=5)
        audio_path = OUTPUT_DIR / f"{job_id}.mp3"
        if not do_tts(job, audio_path):
            raise RuntimeError("TTS failed")
        update_status(job_id, "tts", progress=15)

        # 2) Lipsync / Blender step
        update_status(job_id, "lipsync", progress=20)
        lipsync_video = OUTPUT_DIR / f"{job_id}_face.mp4"
        if not do_lipsync(job, audio_path, lipsync_video):
            raise RuntimeError("Lipsync failed")
        update_status(job_id, "lipsync", progress=45)

        # 3) Render / Stitch scenes (placeholder)
        update_status(job_id, "rendering", progress=50)
        # placeholder final video path
        final_local = OUTPUT_DIR / f"{job_id}.mp4"
        if not combine_assets(job, lipsync_video, final_local):
            raise RuntimeError("Combine failed")
        update_status(job_id, "combining", progress=85)

        # 4) Optionally upload to S3 (skipped here). We just mark success and expose local path.
        set_result_video(job_id, str(final_local))
        LOG.info("Render completed for %s", job_id)
        return {"ok": True, "video": str(final_local)}
    except Exception as exc:
        tb = traceback.format_exc()
        LOG.exception("Render job failed %s", exc)
        # write job failed
        job = read_job(job_id) or {"id": job_id}
        job["status"] = "failed"
        job["error"] = str(exc) + "\n" + tb[:2000]
        job["completed_at"] = datetime.utcnow().isoformat() + "Z"
        write_job(job)
        raise

# helper enqueue function if used as library
def enqueue_render(job_id: str):
    # queue name explicit
    render_job_task.apply_async(args=[job_id], queue="visora_render_queue")
    LOG.info("Enqueued job %s to visora_render_queue", job_id)

# optionally export for import by app
if __name__ == "__main__":
    # quick manual test
    print("Celery worker module - not to be run directly")
