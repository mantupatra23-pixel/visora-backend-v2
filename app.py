"""
app.py
Main Flask app that exposes /create-video and job status endpoints.
Designed to use your local engine modules (generator_3d, template_engine,
voiceclone, lipsync, mixer, postprocess, etc.) to produce a final MP4.

How it works (flow):
1. Client POST /create-video with JSON { "script": "...", "preset": "...", "callback_url": "... (optional)" }
2. Server creates a unique job id, writes a job folder.
3. Server calls engine functions in sequence:
   - template_engine.parse_script -> scene_spec
   - generator_3d.render_scene(scene_spec, out_dir) -> raw_frames/scene_video
   - voiceclone/synthesize_audio -> audio_file
   - lipsync.generate_lipsync -> visemes/timing
   - mixer.combine(video, audio) -> merged.mp4
   - postprocess.optimize -> final.mp4
4. Server returns JSON { job_id, status_url } immediately (async) or can run sync if requested.
5. Client can GET /job/<job_id> to check status and download.

IMPORTANT:
- Replace module imports with your exact module paths if different.
- This implementation uses a simple disk-based job queue and background ThreadPoolExecutor.
- For production you may switch to Redis/RQ or Celery (requirements already have redis/rq).
"""

import os
import uuid
import json
import shutil
import traceback
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from flask import Flask, request, jsonify, send_file, abort

# ---------- CONFIG ----------
BASE_DIR = Path(__file__).resolve().parent
JOBS_DIR = BASE_DIR / "jobs"
OUTPUT_DIR = BASE_DIR / "public" / "videos"
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "2"))  # parallel renderers
ALLOWED_PRESETS = ["reel", "short", "cinematic", "fullhd", "ultrahd"]

# make dirs
JOBS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ---------- IMPORT YOUR ENGINES ----------
# Replace these imports with your actual module paths if different.
# Example: from engine.generator_3d import render_scene
# Example: from engine.template_engine import parse_script
try:
    from generator_3d import render_scene           # expected function: render_scene(scene_spec, out_dir, params) -> dict(status, files)
except Exception:
    # fallback import path
    try:
        from engine.generator_3d import render_scene
    except Exception:
        render_scene = None

try:
    from template_engine import parse_script        # expected function: parse_script(script_text, preset) -> scene_spec (dict)
except Exception:
    try:
        from engine.template_engine import parse_script
    except Exception:
        parse_script = None

# Optional components (voice, lipsync, mixer, postprocess)
try:
    from voiceclone import synthesize_voice         # synthesize_voice(text, voice_params, out_path) -> filepath
except Exception:
    synthesize_voice = None

try:
    from lipsync import apply_lipsync               # apply_lipsync(audio_path, scene_spec, out_dir) -> updates scene frames or timing
except Exception:
    apply_lipsync = None

try:
    from mixer import combine_audio_video           # combine_audio_video(video_path, audio_path, out_path) -> filepath
except Exception:
    combine_audio_video = None

try:
    from postprocess import optimize_video         # optimize_video(in_path, out_path, params) -> filepath
except Exception:
    optimize_video = None

# ---------- APP ----------
app = Flask(__name__)
executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

# job store (simple JSON per job)
def job_file(job_id):
    return JOBS_DIR / f"{job_id}.json"

def save_job_meta(job_id, data):
    with open(job_file(job_id), "w") as f:
        json.dump(data, f, indent=2, default=str)

def load_job_meta(job_id):
    p = job_file(job_id)
    if not p.exists():
        return None
    return json.load(open(p, "r"))

# helper to mark job status
def update_job_status(job_id, status, extra=None):
    meta = load_job_meta(job_id) or {}
    meta["status"] = status
    meta["updated_at"] = datetime.utcnow().isoformat()
    if extra:
        meta.setdefault("history", []).append({"ts": datetime.utcnow().isoformat(), "note": extra})
    save_job_meta(job_id, meta)

# ---------- VIDEO PIPELINE (worker) ----------
def worker_render(job_id):
    """
    Background worker that performs the rendering pipeline for a job.
    It updates the job metadata file along the way.
    """
    meta = load_job_meta(job_id)
    if meta is None:
        return

    try:
        update_job_status(job_id, "started", "Worker picked job")
        job_dir = JOBS_DIR / job_id
        job_dir.mkdir(exist_ok=True)
        script = meta.get("script")
        preset = meta.get("preset", "cinematic")

        # 1) parse script -> scene_spec
        update_job_status(job_id, "parsing", "Parsing script to scene spec")
        if parse_script is None:
            raise RuntimeError("parse_script() module not found. Check imports.")
        scene_spec = parse_script(script, preset=preset)
        # save spec
        with open(job_dir / "scene_spec.json", "w") as f:
            json.dump(scene_spec, f, indent=2, default=str)

        # 2) generate 3D/video frames or primary video asset
        update_job_status(job_id, "rendering", "Rendering scene to frames/video (generator_3d)")
        if render_scene is None:
            raise RuntimeError("render_scene() module not found. Check imports.")
        render_params = meta.get("render_params", {})
        render_out = job_dir / "render_out"
        render_out.mkdir(exist_ok=True)
        # expect render_scene returns dict with keys: 'status' and 'video' or 'frames'
        result = render_scene(scene_spec, str(render_out), render_params)
        if not isinstance(result, dict):
            raise RuntimeError("render_scene() must return a dict with output paths.")
        # find primary video file
        primary_video = result.get("video") or result.get("output")
        if primary_video:
            primary_video = Path(primary_video)
        else:
            # maybe render produced frames; user should provide frame->video step inside render_scene
            # fallback: look for any mp4 inside render_out
            mp4s = list(render_out.glob("*.mp4"))
            primary_video = mp4s[0] if mp4s else None

        if primary_video is None or not primary_video.exists():
            raise RuntimeError("Primary rendered video not found in render output.")

        update_job_status(job_id, "render_done", f"Primary render produced {primary_video.name}")

        # 3) generate voice (optional)
        audio_file = None
        if synthesize_voice is not None:
            update_job_status(job_id, "synthesizing_audio", "Generating voice audio")
            voice_params = meta.get("voice", {})
            audio_path = job_dir / "voice.wav"
            audio_file = synthesize_voice(script, voice_params, str(audio_path))
            update_job_status(job_id, "audio_ready", f"Audio produced {audio_file}")

        # 4) lipsync (optional) - align mouth animation
        if apply_lipsync is not None and audio_file:
            update_job_status(job_id, "lipsync", "Applying lipsync")
            apply_lipsync(str(audio_file), scene_spec, str(render_out))
            update_job_status(job_id, "lipsync_done", "Lipsync applied")

        # 5) combine audio + video
        final_raw = job_dir / "merged_raw.mp4"
        if audio_file and combine_audio_video is not None:
            update_job_status(job_id, "combining", "Combining audio & video")
            merged_path = combine_audio_video(str(primary_video), str(audio_file), str(final_raw))
            final_video_path = Path(merged_path)
        else:
            # no audio or no mixer -> use primary_video as final_raw
            final_video_path = primary_video

        update_job_status(job_id, "combined", f"Combined video at {final_video_path.name}")

        # 6) postprocess / optimize
        final_pub = OUTPUT_DIR / f"{job_id}.mp4"
        if optimize_video is not None:
            update_job_status(job_id, "postprocessing", "Optimizing final video")
            optimize_video(str(final_video_path), str(final_pub), params=meta.get("postprocess", {}))
        else:
            # simple copy if no postprocess
            shutil.copyfile(str(final_video_path), str(final_pub))

        update_job_status(job_id, "done", f"Final video ready at {final_pub}")
        # store result
        meta = load_job_meta(job_id) or {}
        meta["result"] = {"video_url": f"/download/{job_id}"}
        save_job_meta(job_id, meta)

        # optional: callback
        callback = meta.get("callback_url")
        if callback:
            try:
                import requests
                requests.post(callback, json={"job_id": job_id, "status": "done", "video_url": meta["result"]["video_url"]}, timeout=5)
            except Exception:
                pass

    except Exception as e:
        tb = traceback.format_exc()
        update_job_status(job_id, "failed", f"{str(e)}")
        meta = load_job_meta(job_id) or {}
        meta["error"] = str(e)
        meta["traceback"] = tb
        save_job_meta(job_id, meta)
        return

# ---------- ROUTES ----------
@app.route("/create-video", methods=["POST"])
def create_video():
    """
    POST JSON:
    {
      "script": "A boy walking in rain, cinematic look",
      "preset": "cinematic",
      "async": true,            # if true returns job id immediately; if false server will try to run synchronously
      "callback_url": "https://...",   # optional: we will POST job done to this URL
      "render_params": { ... }  # optional params forwarded to generator
    }
    """
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"ok": False, "error": "Invalid or missing JSON body"}), 400

    script = data.get("script") or data.get("inputs") or data.get("prompt")
    if not script:
        return jsonify({"ok": False, "error": "Missing 'script' / 'prompt' field"}), 400

    preset = data.get("preset", "cinematic")
    if preset not in ALLOWED_PRESETS:
        preset = "cinematic"

    job_id = uuid.uuid4().hex[:16]
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    meta = {
        "job_id": job_id,
        "created_at": datetime.utcnow().isoformat(),
        "status": "queued",
        "script": script,
        "preset": preset,
        "render_params": data.get("render_params", {}),
        "callback_url": data.get("callback_url"),
    }
    save_job_meta(job_id, meta)

    # run in background or synchronous
    run_async = data.get("async", True)
    if run_async:
        executor.submit(worker_render, job_id)
        return jsonify({"ok": True, "job_id": job_id, "status_url": f"/job/{job_id}"}), 202
    else:
        # run sync (blocking) - not recommended for long renders
        worker_render(job_id)
        meta = load_job_meta(job_id)
        if meta.get("status") == "done":
            return jsonify({"ok": True, "job_id": job_id, "video_url": meta["result"]["video_url"]}), 200
        else:
            return jsonify({"ok": False, "job_id": job_id, "status": meta.get("status"), "error": meta.get("error")}), 500

@app.route("/job/<job_id>", methods=["GET"])
def job_status(job_id):
    meta = load_job_meta(job_id)
    if not meta:
        return jsonify({"ok": False, "error": "job not found"}), 404
    # if finished, provide download link
    if meta.get("status") == "done" and meta.get("result"):
        return jsonify({"ok": True, "job": meta}), 200
    return jsonify({"ok": True, "job": meta}), 200

@app.route("/download/<job_id>", methods=["GET"])
def download_video(job_id):
    final = OUTPUT_DIR / f"{job_id}.mp4"
    if not final.exists():
        return jsonify({"ok": False, "error": "file not ready"}), 404
    # send file
    return send_file(str(final), mimetype="video/mp4", as_attachment=True, attachment_filename=f"{job_id}.mp4")

# health
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "status": "alive", "workers": MAX_WORKERS})

# ---------- MAIN ----------
if __name__ == "__main__":
    # for local dev only
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=False)
