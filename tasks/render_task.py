from services.celery_app import celery_app
from models import Job
from services.tts_elevenlabs import elevenlabs_tts
from services.lipsync_wav2lip import run_wav2lip
from engines.blender_runner import run_blender_scene
from services.storage import upload_to_s3_if_configured
import logging
import os
from datetime import datetime

logger = logging.getLogger("render_task")

@celery_app.task(name="tasks.render_task.render_job_task", bind=True)
def render_job_task(self, job_id: str):
    job = Job.get_sync(job_id)
    if not job:
        return {"ok": False, "error": "job_not_found"}

    try:
        job.status = "started"
        job.save_sync()

        job.status = "parsing"
        job.save_sync()
        scene_file = job.create_scene_file()

        job.status = "generating_audio"
        job.save_sync()
        audio_file = f"/tmp/{job_id}_tts.wav"
        elevenlabs_tts(job.script_text, audio_file)

        job.status = "lipsync"
        job.save_sync()
        lip_video = f"/tmp/{job_id}_lipsync.mp4"
        run_wav2lip(audio_file, job.avatar_reference_path, lip_video)

        job.status = "rendering"
        job.save_sync()
        render_out = f"/tmp/{job_id}_render.mp4"
        run_blender_scene(scene_file, lip_video, render_out, job.render_settings or {})

        job.status = "postprocessing"
        job.save_sync()
        final_out = f"/opt/render/project/src/public/videos/{job_id}.mp4"
        os.makedirs(os.path.dirname(final_out), exist_ok=True)

        cmd = f"ffmpeg -y -i {render_out} -c:v libx264 -preset slow -crf 18 -c:a aac {final_out}"
        os.system(cmd)

        job.status = "uploading"
        job.save_sync()

        s3_url = upload_to_s3_if_configured(final_out, f"videos/{job_id}.mp4")

        job.status = "completed"
        job.result = {"video_url": s3_url or f"/videos/{job_id}.mp4"}
        job.completed_at = datetime.utcnow()
        job.save_sync()

        return {"ok": True, "job_id": job_id, "url": job.result["video_url"]}

    except Exception as e:
        job.status = "failed"
        job.error = str(e)
        job.save_sync()
        raise
