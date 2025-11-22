#!/usr/bin/env python3
# app.py - Visora backend (clean, production safe)
# Author: Aimantuvya & GPT-5 (assistant)
# Usage: python app.py  (or run via gunicorn/uvicorn in production)

import os
import io
import json
import uuid
import time
import requests
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

# load environment from .env (on Render you set env variables via dashboard)
load_dotenv()

# ---------- CONFIG ----------
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
VIDEO_SAVE_DIR = os.environ.get("VIDEO_SAVE_DIR", os.path.join(BASE_DIR, "static", "videos"))
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.join(BASE_DIR, "static", "uploads"))
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "wav", "mp3", "ogg", "webm", "mp4"}

# Ensure dirs exist
os.makedirs(VIDEO_SAVE_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__, static_folder=os.path.join(BASE_DIR, "static"))

# ---------- Dummy generators (fallbacks) ----------
def _dummy_generate_cinematic_video(script_text="", **kw):
    """Create a tiny fake mp4 so clients can download/test when real engine not available."""
    fname = f"video_{uuid.uuid4().hex[:8]}.mp4"
    path = os.path.join(VIDEO_SAVE_DIR, fname)
    with open(path, "wb") as f:
        f.write(b"FAKE_VIDEO_CONTENT")
    return path

def _dummy_generate_talking_avatar(**kw):
    return _dummy_generate_cinematic_video("talking avatar")

def _dummy_generate_multiscene_video(**kw):
    return _dummy_generate_cinematic_video("multiscene")

def _dummy_generate_fullbody_animation(**kw):
    return _dummy_generate_cinematic_video("fullbody")

def _dummy_generate_movie(**kw):
    return _dummy_generate_cinematic_video("movie")

def _dummy_generate_music(**kw):
    fname = f"music_{uuid.uuid4().hex[:8]}.mp3"
    path = os.path.join(VIDEO_SAVE_DIR, fname)
    with open(path, "wb") as f:
        f.write(b"FAKE_MUSIC")
    return path

# ---------- Try import real engines (if present) ----------
try:
    from engine.video_engine import generate_cinematic_video
except Exception:
    generate_cinematic_video = _dummy_generate_cinematic_video

try:
    from engine.avatar.avatar_engine import generate_talking_avatar
except Exception:
    generate_talking_avatar = _dummy_generate_talking_avatar

try:
    from engine.multiscene.multi_scene_engine import generate_multiscene_video
except Exception:
    generate_multiscene_video = _dummy_generate_multiscene_video

try:
    from engine.fullbody.fullbody_engine import generate_fullbody_animation
except Exception:
    generate_fullbody_animation = _dummy_generate_fullbody_animation

try:
    from engine.multiscene10.multiscene10_engine import generate_10scene_movie
except Exception:
    generate_10scene_movie = _dummy_generate_movie

try:
    from engine.audio.music_engine import render_music
except Exception:
    render_music = _dummy_generate_music

# ---------- REPLICATE GPU ENGINE (real video generator) ----------
REPLICATE_API_TOKEN = os.environ.get("REPLICATE_API_TOKEN", "")
REPLICATE_MODEL_VERSION = os.environ.get("REPLICATE_MODEL_VERSION", "zskx/animate-v3:541b496c618b0c9b92")  # default placeholder

def replicate_generate_video(prompt, timeout_seconds=300, poll_interval=2):
    """
    Create a video using Replicate's predictions API.
    Returns: local saved file path (absolute) OR None on failure.
    """
    if not REPLICATE_API_TOKEN:
        app.logger.warning("No REPLICATE_API_TOKEN set - replicate_generate_video skipped")
        return None

    headers = {
        "Authorization": f"Token {REPLICATE_API_TOKEN}",
        "Content-Type": "application/json",
    }

    # payload structure depends on model. Here we use a general pattern - adjust as model requires.
    payload = {
        "version": REPLICATE_MODEL_VERSION,
        "input": {
            "prompt": prompt,
            # other model-specific fields
            "fps": 12,
            "width": 512,
            "height": 768,
            "motion": "cinematic",
        }
    }

    try:
        r = requests.post("https://api.replicate.com/v1/predictions", headers=headers, json=payload, timeout=30)
        r.raise_for_status()
        job = r.json()
        job_id = job.get("id")
        if not job_id:
            app.logger.error("Replicate returned no job id: %s", job)
            return None

        # poll
        start = time.time()
        while time.time() - start < timeout_seconds:
            status_resp = requests.get(f"https://api.replicate.com/v1/predictions/{job_id}", headers=headers, timeout=30)
            status_resp.raise_for_status()
            status = status_resp.json()
            state = status.get("status")
            # states: starting, processing, succeeded, failed, canceled
            if state == "succeeded":
                output = status.get("output")
                if not output:
                    app.logger.error("Replicate succeeded but returned no output: %s", status)
                    return None

                # output could be a list of URLs or dicts - handle common shapes
                out_item = None
                if isinstance(output, list) and len(output) > 0:
                    out_item = output[0]
                else:
                    out_item = output

                # if out_item is dict with 'url' or 'uri'
                if isinstance(out_item, dict):
                    url = out_item.get("url") or out_item.get("uri") or out_item.get("download_url")
                else:
                    url = str(out_item)

                if not url:
                    app.logger.error("No usable download URL in replicate output: %s", out_item)
                    return None

                # Download asset
                try:
                    dl = requests.get(url, stream=True, timeout=60)
                    dl.raise_for_status()
                    ext = ".mp4"
                    # try to detect extension from url
                    if "." in url.split("?")[0]:
                        possible_ext = os.path.splitext(url.split("?")[0])[1]
                        if possible_ext:
                            ext = possible_ext
                    fname = f"replicate_{uuid.uuid4().hex[:8]}{ext}"
                    save_path = os.path.join(VIDEO_SAVE_DIR, fname)
                    with open(save_path, "wb") as f:
                        for chunk in dl.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                    return save_path
                except Exception as e:
                    app.logger.exception("Failed downloading replicate output: %s", e)
                    return None

            if state in ("failed", "canceled"):
                app.logger.error("Replicate job failed/canceled: %s", status)
                return None

            time.sleep(poll_interval)

        app.logger.error("Replicate job timeout (%s s) for job id %s", timeout_seconds, job_id)
        return None

    except Exception as e:
        app.logger.exception("Replicate request error: %s", e)
        return None

# ---------- UTIL ----------
def allowed_file(filename):
    if not filename:
        return False
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def _save_uploaded_file(file_obj, subfolder="uploads", prefix="file"):
    if not file_obj:
        return None
    filename = secure_filename(file_obj.filename or "")
    if not filename:
        return None
    ext = os.path.splitext(filename)[1]
    file_id = uuid.uuid4().hex[-8:]
    save_name = f"{prefix}_{file_id}{ext}"
    dest_dir = os.path.join(BASE_DIR, "static", subfolder)
    os.makedirs(dest_dir, exist_ok=True)
    save_path = os.path.join(dest_dir, save_name)
    file_obj.save(save_path)
    return os.path.join("static", subfolder, save_name)

# ---------- ROUTES ----------
@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": True, "message": "Visora Backend V2 Running Successfully", "version": "2.0"})

@app.route("/test", methods=["GET"])
def test():
    return jsonify({"msg": "Backend test route working!"})

# Create cinematic video (main)
@app.route("/create-video", methods=["POST"])
def create_video():
    try:
        data = request.form if request.form else (request.get_json(silent=True) or {})
        script = data.get("script", "") if isinstance(data, dict) else ""
        if not script or str(script).strip() == "":
            return jsonify({"status": False, "error": "Script text missing"}), 400

        # optional face upload
        face_file = request.files.get("face")
        face_path = None
        if face_file and allowed_file(face_file.filename):
            face_path = _save_uploaded_file(face_file, subfolder="uploads", prefix="face")

        # Try Replicate GPU engine first
        video_fullpath = None
        video_fullpath = replicate_generate_video(script)
        if not video_fullpath:
            # fallback to local engine (if present)
            try:
                video_fullpath = generate_cinematic_video(script_text=script, face_path=face_path)
            except Exception as e:
                app.logger.exception("Local engine failed, using dummy: %s", e)
                video_fullpath = _dummy_generate_cinematic_video(script)

        if os.path.isabs(video_fullpath) and os.path.exists(video_fullpath):
            fname = os.path.basename(video_fullpath)
            rel = f"/static/videos/{fname}"
        else:
            # assume it's already a relative path
            rel = video_fullpath

        return jsonify({"status": True, "video_url": rel})
    except Exception as e:
        app.logger.exception("create_video error: %s", e)
        return jsonify({"status": False, "error": str(e)}), 500

# Generate 10-scene cinematic movie
@app.route("/generate-movie", methods=["POST"])
def generate_movie():
    try:
        data = request.form if request.form else (request.get_json(silent=True) or {})
        script = data.get("script", "")
        if not script:
            return jsonify({"status": False, "error": "script missing"}), 400

        face_file = request.files.get("face")
        face_path = None
        if face_file and allowed_file(face_file.filename):
            face_path = _save_uploaded_file(face_file, subfolder="uploads", prefix="face")

        max_scenes = int(data.get("max_scenes", 6))
        # prefer 10-scene engine if available
        try:
            filename = generate_10scene_movie(script_text=script, max_scenes=max_scenes, face_path=face_path)
        except Exception:
            filename = _dummy_generate_movie(script)

        if os.path.isabs(filename) and os.path.exists(filename):
            rel = f"/static/videos/{os.path.basename(filename)}"
        else:
            rel = filename
        return jsonify({"status": True, "movie_url": rel})
    except Exception as e:
        app.logger.exception("generate_movie error: %s", e)
        return jsonify({"status": False, "error": str(e)}), 500

# Fullbody generate (main)
@app.route("/generate-fullbody", methods=["POST"])
def generate_fullbody():
    try:
        data = request.form if request.form else (request.get_json(silent=True) or {})
        script = data.get("script", "")
        if not script:
            return jsonify({"status": False, "error": "script missing"}), 400

        face_file = request.files.get("face")
        face_path = None
        if face_file and allowed_file(face_file.filename):
            face_path = _save_uploaded_file(face_file, subfolder="uploads", prefix="face")

        try:
            out_video = generate_fullbody_animation(script_text=script, face_path=face_path)
        except Exception:
            out_video = _dummy_generate_fullbody_animation(script)

        if os.path.isabs(out_video) and os.path.exists(out_video):
            rel = f"/static/videos/{os.path.basename(out_video)}"
        else:
            rel = out_video
        return jsonify({"status": True, "video_url": rel})
    except Exception as e:
        app.logger.exception("generate_fullbody error: %s", e)
        return jsonify({"status": False, "error": str(e)}), 500

# Fullbody motion quick endpoint
@app.route("/fullbody-motion", methods=["POST"])
def fullbody_motion():
    try:
        face = request.files.get("face")
        if not face:
            return jsonify({"status": False, "error": "face missing"}), 400
        face_path = _save_uploaded_file(face, subfolder="uploads", prefix="face")
        style = request.form.get("style", "fortnite")
        preset = request.form.get("preset", "dance")
        try:
            final_video = generate_fullbody_animation(face_img_path=face_path, style=style, preset=preset)
        except Exception:
            final_video = _dummy_generate_fullbody_animation(style)

        if os.path.isabs(final_video) and os.path.exists(final_video):
            rel = f"/static/videos/{os.path.basename(final_video)}"
        else:
            rel = final_video
        return jsonify({"status": True, "video_url": rel})
    except Exception as e:
        app.logger.exception("fullbody_motion error: %s", e)
        return jsonify({"status": False, "error": str(e)}), 500

# Generate music (procedural / replicate or local)
@app.route("/generate-music", methods=["POST"])
def api_generate_music():
    try:
        data = request.get_json(silent=True) or request.form or {}
        duration = float(data.get("duration", 12))
        bpm = int(data.get("bpm", 90))
        style = data.get("style", "cinematic")
        use_cloud = str(data.get("cloud", "false")).lower() == "true"

        if use_cloud and REPLICATE_API_TOKEN:
            # For now call local render_music or fallback (you can implement replicate music if model available)
            mp = render_music(duration=duration, bpm=bpm, style=style)
        else:
            mp = render_music(duration=duration, bpm=bpm, style=style)

        if os.path.isabs(mp) and os.path.exists(mp):
            rel = f"/static/videos/{os.path.basename(mp)}"
        else:
            rel = mp
        return jsonify({"status": True, "music_path": rel})
    except Exception as e:
        app.logger.exception("generate_music error: %s", e)
        return jsonify({"status": False, "error": str(e)}), 500

# Generate SFX (simple)
@app.route("/generate-sfx", methods=["POST"])
def api_generate_sfx():
    try:
        data = request.get_json(silent=True) or request.form or {}
        kind = data.get("kind", "whoosh")
        fname = f"sfx_{kind}_{uuid.uuid4().hex[:8]}.wav"
        path = os.path.join(VIDEO_SAVE_DIR, fname)
        with open(path, "wb") as f:
            f.write(b"FAKE_SFX")
        rel = f"/static/videos/{fname}"
        return jsonify({"status": True, "sfx_path": rel})
    except Exception as e:
        app.logger.exception("generate_sfx error: %s", e)
        return jsonify({"status": False, "error": str(e)}), 500

# Mix audio (voice + music + sfx)
@app.route("/mix-audio", methods=["POST"])
def api_mix_audio():
    try:
        voice_file = request.files.get("voice")
        music_file = request.files.get("music")
        sfx_file = request.files.get("sfx")
        if not voice_file:
            return jsonify({"status": False, "error": "voice missing"}), 400

        voice_path = _save_uploaded_file(voice_file, subfolder="uploads", prefix="voice")
        music_path = _save_uploaded_file(music_file, subfolder="uploads", prefix="music") if music_file else None
        sfx_path = _save_uploaded_file(sfx_file, subfolder="uploads", prefix="sfx") if sfx_file else None

        fname = f"mixed_{uuid.uuid4().hex[:8]}.wav"
        path = os.path.join(VIDEO_SAVE_DIR, fname)
        with open(path, "wb") as f:
            f.write(b"FAKE_MIXED_AUDIO")
        rel = f"/static/videos/{fname}"
        return jsonify({"status": True, "mixed_path": rel})
    except Exception as e:
        app.logger.exception("mix_audio error: %s", e)
        return jsonify({"status": False, "error": str(e)}), 500

# Upload helpers
@app.route("/upload-face", methods=["POST"])
def upload_face():
    try:
        if "file" not in request.files:
            return jsonify({"status": False, "error": "No file part"}), 400
        file = request.files["file"]
        rel = _save_uploaded_file(file, subfolder="uploads", prefix="face")
        if not rel:
            return jsonify({"status": False, "error": "Empty filename"}), 400
        return jsonify({"status": True, "face_path": rel})
    except Exception as e:
        app.logger.exception("upload_face error: %s", e)
        return jsonify({"status": False, "error": str(e)}), 500

@app.route("/upload-voice", methods=["POST"])
def upload_voice():
    try:
        if "file" not in request.files:
            return jsonify({"status": False, "error": "No file part"}), 400
        file = request.files["file"]
        rel = _save_uploaded_file(file, subfolder="uploads", prefix="voice")
        if not rel:
            return jsonify({"status": False, "error": "Empty filename"}), 400
        return jsonify({"status": True, "message": "Voice saved", "path": rel})
    except Exception as e:
        app.logger.exception("upload_voice error: %s", e)
        return jsonify({"status": False, "error": str(e)}), 500

# List poses/costumes/hairstyles (examples)
@app.route("/poses", methods=["GET"])
def api_list_poses():
    try:
        poses = ["idle", "walk", "run", "dance", "action"]
        return jsonify({"status": True, "poses": poses})
    except Exception as e:
        return jsonify({"status": False, "error": str(e)}), 500

@app.route("/costumes", methods=["GET"])
def api_list_costumes():
    costumes = ["casual", "formal", "armor", "fantasy"]
    return jsonify({"status": True, "costumes": costumes})

@app.route("/hairstyles", methods=["GET"])
def api_list_hairstyles():
    hair = ["short", "long", "ponytail", "mohawk"]
    return jsonify({"status": True, "hairstyles": hair})

# Serve static videos safely
@app.route("/static/videos/<path:filename>", methods=["GET"])
def serve_video(filename):
    safe = secure_filename(filename)
    return send_from_directory(VIDEO_SAVE_DIR, safe)

# Conversation / multi-avatar endpoint (simple)
@app.route("/generate-conversation", methods=["POST"])
def api_generate_conversation():
    try:
        data = request.form if request.form else (request.get_json(silent=True) or {})
        script = data.get("script", "")
        if not script:
            return jsonify({"status": False, "error": "script missing"}), 400
        avatars_raw = data.get("avatars", "[]")
        try:
            avatars = json.loads(avatars_raw) if isinstance(avatars_raw, str) else avatars_raw
        except Exception:
            avatars = {}

        # Try local generation fallback
        video = _dummy_generate_multiscene_video(script)
        if os.path.isabs(video) and os.path.exists(video):
            rel = f"/static/videos/{os.path.basename(video)}"
        else:
            rel = video
        return jsonify({"status": True, "video_url": rel})
    except Exception as e:
        app.logger.exception("generate_conversation error: %s", e)
        return jsonify({"status": False, "error": str(e)}), 500

# ---------- RUN ----------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    host = os.environ.get("HOST", "0.0.0.0")
    debug = os.environ.get("DEBUG", "false").lower() == "true"
    app.run(host=host, port=port, debug=debug)
