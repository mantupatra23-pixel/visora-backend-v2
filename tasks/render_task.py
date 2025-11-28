# tasks/render_task.py
"""
Celery render task - production ready.

यह file:
 - jobs/<job_id>.json पढ़कर job process करता है
 - TTS (ElevenLabs) बनाता है
 - अगर face_video मौजूद है तो Wav2Lip से lipsync करता है
 - अन्यथा Blender से scene render कर के audio के साथ merge करता है
 - final mp4 को local public folder में रखता है या S3 पर upload करता है
 - job JSON में status/result update करता है

Usage (worker host पर):
    export REDIS_URL=redis://127.0.0.1:6379/0
    celery -A tasks.render_task.celery_app worker -Q renderers -c 1 --loglevel=info

Required env:
    BASE_URL, REDIS_URL, VIDEO_SAVE_DIR, JOBS_DIR, S3_BUCKET, AWS_*, ELEVENLABS_API_KEY,
    WAV2LIP_CHECKPOINT, BLENDER_BIN, BLENDER_SCRIPT
"""

import os
import json
import logging
import traceback
from pathlib import Path
from datetime import datetime

# try to reuse existing celery app if present (services.celery_app)
try:
    from services.celery_app import celery_app
    logging.getLogger("tasks.render_task").info("Using services.celery_app.celery_app")
except Exception:
    # fallback: create local celery app
    from celery import Celery
    REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
    celery_app = Celery("visora_render", broker=REDIS_URL, backend=REDIS_URL)
    logging.getLogger("tasks.render_task").info("Created local Celery app with broker %s", REDIS_URL)

# engines (these modules should exist in engines/)
from engines import tts_elevenlabs, wav2lip_runner, blender_runner, postprocess

LOG = logging.getLogger("tasks.render_task")
LOG.setLevel(logging.INFO)

# paths (can be overridden by env)
JOBS_DIR = Path(os.environ.get("JOBS_DIR", "jobs"))
OUTPUT_DIR = Path(os.environ.get("VIDEO_SAVE_DIR", "public/videos"))
JOBS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")
S3_BUCKET = os.environ.get("S3_BUCKET", "")

def read_job_file(job_id: str) -> dict:
    p = JOBS_DIR / f"{job_id}.json"
    if not p.exists():
        raise FileNotFoundError(f"Job file not found: {p}")
    return json.loads(p.read_text(encoding="utf-8"))

def write_job_file(job_id: str, data: dict):
    p = JOBS_DIR / f"{job_id}.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def update_job_status(job_id: str, status: str, extra: dict = None):
    try:
        job = read_job_file(job_id)
    except Exception:
        job = {"id": job_id}
    job["status"] = status
    job.setdefault("meta", {})
    job["meta"]["last_update_at"] = datetime.utcnow().isoformat()
    if extra:
        job.setdefault("meta", {}).update(extra)
    write_job_file(job_id, job)
    LOG.info("Job %s status -> %s", job_id, status)

def set_job_result(job_id: str, result: dict):
    try:
        job = read_job_file(job_id)
    except Exception:
        job = {"id": job_id}
    job["result"] = result
    job["status"] = "completed"
    job["completed_at"] = datetime.utcnow().isoformat()
    write_job_file(job_id, job)
    LOG.info("Job %s completed, result set", job_id)

def set_job_failed(job_id: str, error: str):
    try:
        job = read_job_file(job_id)
    except Exception:
        job = {"id": job_id}
    job["status"] = "failed"
    job["error"] = error
    job["completed_at"] = datetime.utcnow().isoformat()
    write_job_file(job_id, job)
    LOG.error("Job %s failed: %s", job_id, error)

@celery_app.task(bind=True, name="tasks.render_task.render_job_task")
def render_job_task(self, job_id: str):
    """
    Celery task entrypoint.
    """
    LOG.info("render_job_task start: %s", job_id)
    try:
        job = read_job_file(job_id)
    except FileNotFoundError as e:
        LOG.exception("Job file missing")
        raise e

    # Basic job fields
    script = job.get("script") or job.get("script_text") or ""
    preset = job.get("preset", "reel")
    face_video = job.get("face_video")  # optional path (absolute or repo path)
    try:
        # 0) set queued/started
        update_job_status(job_id, "started")

        # 1) TTS
        update_job_status(job_id, "tts")
        LOG.info("Job %s: generating TTS", job_id)
        # safe filename
        tts_filename = f"{job_id}_tts.mp3"
        try:
            audio_path = tts_elevenlabs.synthesize_voice(script, filename=tts_filename)
        except Exception as e:
            LOG.exception("TTS failed, creating silent placeholder")
            # create a silent fallback wav/mp3 of 1s using ffmpeg if available, else empty file
            fallback = OUTPUT_DIR / f"{job_id}_tts_silent.mp3"
            try:
                # try use ffmpeg to create 1-second silent audio (if ffmpeg present)
                import subprocess
                subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                                "-t", "1", str(fallback)],
                               check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                audio_path = str(fallback)
            except Exception:
                LOG.exception("Failed to create silent audio fallback")
                fallback.write_text("")  # empty file
                audio_path = str(fallback)

        LOG.info("Job %s: audio at %s", job_id, audio_path)

        # 2) Lipsync OR Blender render
        if face_video:
            update_job_status(job_id, "lipsync")
            LOG.info("Job %s: doing Wav2Lip with face_video=%s", job_id, face_video)
            out_lipsync = OUTPUT_DIR / f"{job_id}_lipsync.mp4"
            # Use wrapper script to ensure checkpoint present
            # Command: python engines/wav2lip_runner.py --face <face_video> --audio <audio_path> --out <out_lipsync>
            try:
                # call ensure via module function if available, else fallback to subprocess wrapper
                rc = wav2lip_runner.run_wav2lip(face_video, audio_path, str(out_lipsync))
                if rc != 0:
                    raise RuntimeError(f"Wav2Lip exited non-zero {rc}")
                used_video = str(out_lipsync)
            except AttributeError:
                # if module has no run_wav2lip, try CLI main
                import subprocess
                cmd = [ "python", str(Path(__file__).resolve().parent.parent / "engines" / "wav2lip_runner.py"),
                        "--face", str(face_video), "--audio", str(audio_path), "--out", str(out_lipsync) ]
                LOG.info("Running wav2lip CLI: %s", " ".join(cmd))
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                LOG.info(proc.stdout)
                if proc.returncode != 0:
                    raise RuntimeError("Wav2Lip failed")
                used_video = str(out_lipsync)

            LOG.info("Job %s: lipsync produced %s", job_id, used_video)

            # combine audio + lipsync (postprocess)
            update_job_status(job_id, "postprocessing")
            final_out = OUTPUT_DIR / f"{job_id}_final.mp4"
            try:
                combined = postprocess.combine_audio_video(used_video, audio_path, out_file=str(final_out))
            except Exception:
                # if combine fails, try to just move lipsync output
                LOG.exception("Combine failed, using lipsync output as final")
                combined = used_video
            final_path = str(final_out) if combined else used_video

        else:
            # No face video -> run Blender to create scene then combine
            update_job_status(job_id, "rendering")
            LOG.info("Job %s: rendering Blender scene", job_id)
            scene_cfg = {
                "script": script,
                "preset": preset,
                "job_id": job_id
            }
            # blender_runner.render_scene expects dict and returns output path
            blender_out = blender_runner.render_scene(scene_cfg, output_filename=f"{job_id}_blender.mp4")
            LOG.info("Job %s: blender out: %s", job_id, blender_out)

            update_job_status(job_id, "postprocessing")
            final_out = OUTPUT_DIR / f"{job_id}_final.mp4"
            try:
                combined = postprocess.combine_audio_video(blender_out, audio_path, out_file=str(final_out))
            except Exception:
                LOG.exception("Combine failed; using blender output")
                combined = blender_out
            final_path = str(final_out) if combined else blender_out

        LOG.info("Job %s: final video at %s", job_id, final_path)

        # 3) Upload to S3 (optional) or set local public URL
        update_job_status(job_id, "uploading")
        video_url = None
        try:
            if S3_BUCKET:
                # use postprocess.upload_to_s3 or services.storage if available
                try:
                    s3info = postprocess.upload_to_s3(final_path, key=(os.environ.get("S3_KEY_PREFIX","videos/") + Path(final_path).name))
                    video_url = s3info.get("url")
                except Exception:
                    LOG.exception("postprocess.upload_to_s3 failed, trying services.storage")
                    # alternative fallback
                    from services.storage import upload_to_s3_if_configured
                    s3url = upload_to_s3_if_configured(final_path, f"videos/{Path(final_path).name}")
                    video_url = s3url
            else:
                # local public URL
                if BASE_URL:
                    video_url = f"{BASE_URL}/videos/{Path(final_path).name}"
                else:
                    video_url = f"/videos/{Path(final_path).name}"
        except Exception:
            LOG.exception("Upload step failed")
            # still allow job to complete with local path
            if BASE_URL:
                video_url = f"{BASE_URL}/videos/{Path(final_path).name}"
            else:
                video_url = f"/videos/{Path(final_path).name}"

        # 4) Finalize job JSON + optional webhook
        set_job_result(job_id, {"video_url": video_url, "path": final_path})
        LOG.info("Job %s done -> %s", job_id, video_url)

        # optional: call callback webhook if present
        try:
            job_meta = read_job_file(job_id).get("meta", {})
            webhook = job_meta.get("webhook_url") or os.environ.get("BACON_URL")
            if webhook:
                import requests
                requests.post(webhook, json={"status":"completed", "job_id": job_id, "video_url": video_url}, timeout=6)
                LOG.info("Webhook fired for job %s to %s", job_id, webhook)
        except Exception:
            LOG.exception("Webhook notify failed")

        return {"ok": True, "video_url": video_url}

    except Exception as exc:
        LOG.exception("Render job failed for %s", job_id)
        err = str(exc) + "\n" + traceback.format_exc()
        set_job_failed(job_id, err)
        # optional webhook for failure
        try:
            job_meta = read_job_file(job_id).get("meta", {})
            webhook = job_meta.get("webhook_url") or os.environ.get("BACON_URL")
            if webhook:
                import requests
                requests.post(webhook, json={"status":"failed", "job_id": job_id, "error": str(exc)}, timeout=6)
        except Exception:
            LOG.exception("Webhook failed on error")
        raise

# End of file
