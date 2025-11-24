# app.py
"""
Visora backend main app

- Exposes /create-video -> accept script JSON and create async job
- /job/<job_id> -> status & meta
- /download/<job_id> -> final mp4
- /health -> quick health check

Design:
- Uses engine.* modules if present:
    - engine.parse_script.parse_script(...)
    - engine.generator_3d.render_scene(...)  (or engine.video_engine.render_scene)
    - engine.character_engine.CharacterEngine (Blender-based)
    - engine.voiceclone.synthesize_voice(...)
    - engine.lipsync.apply_lipsync(...)
    - engine.mixer.combine_audio_video(...)
    - engine.postprocess.optimize_video(...)
- If missing, falls back to simple placeholders (no crash)
"""

import os
import json
import uuid
import shutil
import traceback
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import sys
sys.path.append(str(Path(__file__).resolve().parent / "engine"))

from flask import Flask, request, jsonify, send_file, abort

# -------------------- CONFIG --------------------
BASE_DIR = Path(__file__).resolve().parent
JOBS_DIR = BASE_DIR / "jobs"
OUTPUT_DIR = BASE_DIR / "public" / "videos"

MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "2"))
ALLOWED_PRESETS = ["reel", "short", "cinematic", "fullhd", "ultra"]

# TTS / ElevenLabs config (if using)
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
S3_BUCKET = os.environ.get("S3_BUCKET", "")  # optional upload target

# create dirs
JOBS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# -------------------- IMPORT YOUR ENGINES (with graceful fallback) --------------------
# parse_script: returns scene_spec dict from text/script
try:
       from parse_script import parse_script
except Exception:
    parse_script = None

# main render scene function (3D/render engine) -> returns dict with keys: status, video (path)
try:
    # try known names
    from engine.generator_3d import render_scene
except Exception:
    try:
        from engine.video_engine import render_scene
    except Exception:
        render_scene = None

# Character engine (blender-based) class
try:
    from engine.character_engine import CharacterEngine
except Exception:
    CharacterEngine = None

# voice generation: can be voiceclone or ElevenLabs adapter
try:
    from engine.voiceclone import synthesize_voice as synth_local_voice
except Exception:
    synth_local_voice = None

# lipsync
try:
    from engine.lipsync import apply_lipsync
except Exception:
    apply_lipsync = None

# mixer
try:
    from engine.mixer import combine_audio_video
except Exception:
    combine_audio_video = None

# postprocess
try:
    from engine.postprocess import optimize_video
except Exception:
    optimize_video = None

# -------------------- FALLBACK HELPERS --------------------
def _now_iso():
    return datetime.now(timezone.utc).isoformat()

def job_file(job_id):
    return JOBS_DIR / f"{job_id}.json"

def save_job_meta(job_id, meta):
    with open(job_file(job_id), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, default=str)

def load_job_meta(job_id):
    p = job_file(job_id)
    if not p.exists():
        return None
    return json.load(open(p, "r", encoding="utf-8"))

def update_job_status(job_id, status, extra=None):
    meta = load_job_meta(job_id) or {}
    meta["status"] = status
    meta.setdefault("history", []).append({"ts": _now_iso(), "status": status, "extra": extra})
    meta["updated_at"] = _now_iso()
    save_job_meta(job_id, meta)

# simple TTS adapter that prefers ElevenLabs if API key set, else local voiceclone if available
def render_voice_for_character(character_meta, out_wav_path):
    """
    character_meta: dict with keys like name, gender, age, dialogue (text), voice_params
    out_wav_path: str path to save wav
    """
    text = character_meta.get("dialogue") or character_meta.get("text") or ""
    # prefer local synth if available
    if synth_local_voice:
        # expected to return path to wav
        return synth_local_voice(text=text, meta=character_meta, out_path=str(out_wav_path))
    # fallback: ElevenLabs via simple HTTP (if key set)
    if ELEVENLABS_API_KEY:
        try:
            # minimal ElevenLabs TTS (requires requests)
            import requests
            voice = character_meta.get("voice", "alloy")  # placeholder
            url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice}"
            headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
            payload = {"text": text}
            r = requests.post(url, headers=headers, json=payload, stream=True, timeout=30)
            if r.status_code in (200, 201):
                with open(out_wav_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=4096):
                        if chunk:
                            f.write(chunk)
                return str(out_wav_path)
        except Exception:
            traceback.print_exc()
    # fallback silent beep (generate empty wav)
    try:
        # write small silent wav (using wave)
        import wave, struct
        framerate = 22050
        duration = max(0.4, len(text.split()) * 0.06)
        nframes = int(duration * framerate)
        with wave.open(out_wav_path, "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(framerate)
            silence = struct.pack("<h", 0)
            for _ in range(nframes):
                wf.writeframes(silence)
        return str(out_wav_path)
    except Exception:
        return None

# lipsync wrapper
def generate_lipsync_map(char, wav_path):
    if apply_lipsync:
        return apply_lipsync(wav_path, character=char)
    # fallback: empty viseme map
    return {"visemes": []}

# mixer wrapper
def combine_audio_and_video(primary_video_path, audio_assets, out_path):
    if combine_audio_video:
        return combine_audio_video(str(primary_video_path), audio_assets, str(out_path))
    # fallback: copy primary to out_path
    shutil.copyfile(str(primary_video_path), str(out_path))
    return str(out_path)

# -------------------- BACKGROUND WORKER --------------------
executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

def worker_render(job_id):
    """Background worker that performs render pipeline for a job."""
    meta = load_job_meta(job_id)
    if not meta:
        return
    try:
        update_job_status(job_id, "started", "Worker picked job")
        job_dir = JOBS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        # parse script -> scene_spec
        script = meta.get("script", "")
        preset = meta.get("preset", "cinematic")
        update_job_status(job_id, "parsing", "Parsing script to scene spec")
        if parse_script is None:
            raise RuntimeError("parse_script() module not found. Add engine/parse_script.py")
        scene_spec = parse_script(script, preset=preset)
        (job_dir / "scene_spec.json").write_text(json.dumps(scene_spec, indent=2), encoding="utf-8")
        update_job_status(job_id, "parsed", "Scene spec created")

        # render primary video (3D / SD / hybrid)
        update_job_status(job_id, "rendering", "Rendering scene")
        render_out = job_dir / "render_out"
        render_out.mkdir(parents=True, exist_ok=True)
        if render_scene is None:
            raise RuntimeError("render_scene() module not found. Add engine/generator_3d.py or engine/video_engine.py")
        # expected: render_scene(scene_spec, out_dir, render_params) -> dict with keys {status, video}
        result = render_scene(scene_spec, str(render_out), meta.get("render_params", {}))
        if not isinstance(result, dict):
            raise RuntimeError("render_scene must return dict { 'status':.., 'video': path }")
        primary_video = result.get("video")
        if not primary_video:
            # fallback: look for any mp4 in render_out
            mp4s = list(render_out.glob("*.mp4"))
            primary_video = str(mp4s[0]) if mp4s else None
        if not primary_video:
            raise RuntimeError("Primary rendered video not found after render_scene")

        update_job_status(job_id, "render_done", f"Primary render at {primary_video}")

        # synthesize voices per character
        audio_assets = []
        characters = scene_spec.get("characters", [])
        for idx, ch in enumerate(characters):
            try:
                update_job_status(job_id, "synth_audio_start", f"Synthesizing audio for {ch.get('name','char')}")
                char_dir = job_dir / f"chars" / f"char{idx}"
                char_dir.mkdir(parents=True, exist_ok=True)
                wav_path = char_dir / f"{ch.get('name','char')}.wav"
                wav_path = render_voice_for_character(ch, out_wav_path=str(wav_path))
                if not wav_path:
                    raise RuntimeError(f"Failed to generate audio for {ch.get('name')}")
                audio_assets.append({"name": ch.get("name","char"), "path": str(wav_path)})
                update_job_status(job_id, "audio_ready", f"Audio ready for {ch.get('name')}")
            except Exception as e:
                update_job_status(job_id, "audio_error", f"{ch.get('name')}: {str(e)}")
                traceback.print_exc()

        # generate lip-sync maps (optional)
        viseme_maps = []
        for ch in characters:
            try:
                wav = None
                for a in audio_assets:
                    if a["name"] == ch.get("name"):
                        wav = a["path"]
                        break
                if wav:
                    viseme_map = generate_lipsync_map(ch, wav)
                    viseme_maps.append({"name": ch.get("name"), "viseme_map": viseme_map})
            except Exception:
                traceback.print_exc()

        # combine primary video & audio -> merged_raw.mp4
        merged_raw = job_dir / "merged_raw.mp4"
        update_job_status(job_id, "combining", "Combining audio + video")
        final_video_path = combine_audio_and_video(primary_video, audio_assets, str(merged_raw))
        update_job_status(job_id, "combined", f"Combined at {final_video_path}")

        # postprocess / optimize
        update_job_status(job_id, "postprocessing", "Postprocessing final video")
        final_pub = OUTPUT_DIR / f"{job_id}.mp4"
        if optimize_video:
            optimize_video(str(final_video_path), str(final_pub))
        else:
            # simple copy
            shutil.copyfile(str(final_video_path), str(final_pub))

        update_job_status(job_id, "done", f"Final video ready at {final_pub}")
        meta = load_job_meta(job_id) or {}
        meta["result"] = {"video_url": f"/download/{job_id}"}
        save_job_meta(job_id, meta)

        # optional: upload to S3 if configured
        if S3_BUCKET:
            try:
                update_job_status(job_id, "uploading", "Uploading to S3")
                import boto3
                s3 = boto3.client("s3")
                s3.upload_file(str(final_pub), S3_BUCKET, f"{job_id}.mp4")
                meta = load_job_meta(job_id) or {}
                meta["result"]["s3_url"] = f"s3://{S3_BUCKET}/{job_id}.mp4"
                save_job_meta(job_id, meta)
                update_job_status(job_id, "uploaded", "Uploaded to S3")
            except Exception:
                traceback.print_exc()

    except Exception as e:
        tb = traceback.format_exc()
        update_job_status(job_id, "failed", str(e))
        meta = load_job_meta(job_id) or {}
        meta["error"] = str(e)
        meta["traceback"] = tb
        save_job_meta(job_id, meta)
        return

# -------------------- FLASK APP / ROUTES --------------------
app = Flask(__name__)

@app.route("/create-video", methods=["POST"])
def create_video():
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"ok": False, "error": "Missing JSON body"}), 400

    # accept multiple keys for script
    script = data.get("script") or data.get("inputs") or data.get("text") or ""
    if not script:
        return jsonify({"ok": False, "error": "Missing 'script' field"}), 400

    preset = data.get("preset", "cinematic")
    if preset not in ALLOWED_PRESETS:
        preset = "cinematic"

    job_id = uuid.uuid4().hex[:16]
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    meta = {
        "job_id": job_id,
        "created_at": _now_iso(),
        "status": "queued",
        "script": script,
        "preset": preset,
        "render_params": data.get("render_params", {}),
        "callback_url": data.get("callback_url"),
    }
    save_job_meta(job_id, meta)

    run_async = data.get("async", True)
    if run_async:
        executor.submit(worker_render, job_id)
        return jsonify({"ok": True, "job_id": job_id, "status_url": f"/job/{job_id}"}), 202
    else:
        # blocking call (not recommended for long runs)
        worker_render(job_id)
        meta = load_job_meta(job_id)
        if meta and meta.get("status") == "done":
            return jsonify({"ok": True, "job_id": job_id, "result": meta.get("result")}), 200
        return jsonify({"ok": False, "job_id": job_id, "status": meta.get("status")}), 500

@app.route("/job/<job_id>", methods=["GET"])
def job_status(job_id):
    meta = load_job_meta(job_id)
    if not meta:
        return jsonify({"ok": False, "error": "job not found"}), 404
    return jsonify({"ok": True, "job": meta}), 200

@app.route("/download/<job_id>", methods=["GET"])
def download_video(job_id):
    final = OUTPUT_DIR / f"{job_id}.mp4"
    if not final.exists():
        return jsonify({"ok": False, "error": "file not ready"}), 404
    return send_file(str(final), mimetype="video/mp4", as_attachment=True, download_name=f"{job_id}.mp4")

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "status": "alive", "workers": MAX_WORKERS}), 200

# -------------------- MAIN --------------------
if __name__ == "__main__":
    # local debug run (not for production)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=False)
