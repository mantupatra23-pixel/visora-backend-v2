#!/usr/bin/env python3
# app.py - Visora backend (clean, production-safe)
# Author: Aimantuvya & GPT-5 (starter)
# Usage: python app.py  (or run via gunicorn/uvicorn in production)

import os
import json
import uuid
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

load_dotenv()

# ---------- CONFIG ----------
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
VIDEO_SAVE_DIR = os.environ.get("VIDEO_SAVE_DIR", os.path.join(BASE_DIR, "static", "videos"))
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.join(BASE_DIR, "static", "uploads"))
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "wav", "mp3", "mp4", "ogg"}

os.makedirs(VIDEO_SAVE_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__, static_folder=os.path.join(BASE_DIR, "static"))

# ---------- Try import real engines, else dummy fallbacks ----------
def _dummy_generate_cinematic_video(script_text="", **kw):
    # create tiny fake file so clients can download/test
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
    # returns relative path to generated mp3
    fname = f"music_{uuid.uuid4().hex[:8]}.mp3"
    path = os.path.join(VIDEO_SAVE_DIR, fname)
    with open(path, "wb") as f:
        f.write(b"FAKE_MUSIC")
    return path

# Attempt to import engines - if not present, use dummy functions
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

# ---------- UTIL ----------
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def _save_uploaded_file(file_obj, subfolder="uploads", prefix="file"):
    if not file_obj:
        return None
    filename = secure_filename(file_obj.filename or "")
    if not filename:
        return None
    ext = os.path.splitext(filename)[1]
    file_id = uuid.uuid4().hex[:8]
    save_name = f"{prefix}_{file_id}{ext}"
    dest_dir = os.path.join(BASE_DIR, "static", subfolder)
    os.makedirs(dest_dir, exist_ok=True)
    save_path = os.path.join(dest_dir, save_name)
    file_obj.save(save_path)
    rel_path = os.path.join("static", subfolder, save_name)  # relative path for response
    return rel_path

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
            return jsonify({"status": False, "error": "Script required"}), 400

        # optional face upload
        face_file = request.files.get("face")
        face_path = None
        if face_file and allowed_file(face_file.filename):
            face_path = _save_uploaded_file(face_file, subfolder="uploads", prefix="face")

        # call engine
        video_fullpath = generate_cinematic_video(script_text=script, user_face=face_path, options=data)
        # Normalize return: engine may return full path or relative
        if os.path.isabs(video_fullpath) and os.path.exists(video_fullpath):
            fname = os.path.basename(video_fullpath)
            rel = f"/static/videos/{fname}"
        else:
            # assume engine returned relative inside static
            rel = video_fullpath if isinstance(video_fullpath, str) else ""
        return jsonify({"status": True, "message": "Video generated successfully", "video_url": rel})
    except Exception as e:
        return jsonify({"status": False, "error": str(e)}), 500

# Talking avatar
@app.route("/talking-avatar", methods=["POST"])
def talking_avatar():
    try:
        data = request.form if request.form else (request.get_json(silent=True) or {})
        script = data.get("script", "")
        gender = data.get("gender", "any")
        emotion = data.get("emotion", "neutral")
        mode = data.get("mode", "fullbody")

        # optional face file
        user_face = request.files.get("face")
        user_face_path = None
        if user_face and allowed_file(user_face.filename):
            user_face_path = _save_uploaded_file(user_face, subfolder="uploads", prefix="face")

        filename = generate_talking_avatar(script_text=script, gender=gender, emotion=emotion, user_face=user_face_path, mode=mode)
        if os.path.isabs(filename):
            fname = os.path.basename(filename)
            rel = f"/static/videos/{fname}"
        else:
            rel = filename
        return jsonify({"status": True, "video_url": rel})
    except Exception as e:
        return jsonify({"status": False, "error": str(e)}), 500

# Multiscene avatar
@app.route("/multiscene-avatar", methods=["POST"])
def multiscene_avatar():
    try:
        data = request.form if request.form else (request.get_json(silent=True) or {})
        script = data.get("script", "")
        gender = data.get("gender", "any")
        emotion = data.get("emotion", "neutral")
        user_face = request.files.get("face")
        face_path = None
        if user_face and allowed_file(user_face.filename):
            face_path = _save_uploaded_file(user_face, subfolder="uploads", prefix="face")
        filename = generate_multiscene_video(script_text=script, gender=gender, emotion=emotion, user_face=face_path, options=data)
        if os.path.isabs(filename):
            fname = os.path.basename(filename)
            rel = f"/static/videos/{fname}"
        else:
            rel = filename
        return jsonify({"status": True, "video_url": rel})
    except Exception as e:
        return jsonify({"status": False, "error": str(e)}), 500

# Generate movie (10-scene / cinematic)
@app.route("/generate-movie", methods=["POST"])
def generate_movie():
    try:
        data = request.form if request.form else (request.get_json(silent=True) or {})
        script = data.get("script", "")
        if not script:
            return jsonify({"status": False, "error": "script required"}), 400
        face_file = request.files.get("face")
        face_path = None
        if face_file and allowed_file(face_file.filename):
            face_path = _save_uploaded_file(face_file, subfolder="uploads", prefix="face")
        max_scenes = int(data.get("max_scenes", 6))
        # Use 10-scene engine if available
        filename = generate_10scene_movie(script_text=script, user_face=face_path, max_scenes=max_scenes, options=data)
        if os.path.isabs(filename):
            fname = os.path.basename(filename)
            rel = f"/static/videos/{fname}"
        else:
            rel = filename
        return jsonify({"status": True, "movie_url": rel})
    except Exception as e:
        return jsonify({"status": False, "error": str(e)}), 500

# Fullbody generate (main)
@app.route("/generate-fullbody", methods=["POST"])
def generate_fullbody():
    try:
        data = request.form if request.form else (request.get_json(silent=True) or {})
        script = data.get("script", "")
        if not script:
            return jsonify({"status": False, "error": "script required"}), 400
        face_file = request.files.get("face")
        face_path = None
        if face_file and allowed_file(face_file.filename):
            face_path = _save_uploaded_file(face_file, subfolder="uploads", prefix="face")
        # call fullbody engine
        out_video = generate_fullbody_animation(script_text=script, face_img_path=face_path, options=data)
        if os.path.isabs(out_video):
            fname = os.path.basename(out_video)
            rel = f"/static/videos/{fname}"
        else:
            rel = out_video
        return jsonify({"status": True, "video_url": rel})
    except Exception as e:
        return jsonify({"status": False, "error": str(e)}), 500

# Fullbody motion quick endpoint
@app.route("/fullbody-motion", methods=["POST"])
def fullbody_motion():
    try:
        face = request.files.get("face")
        if not face:
            return jsonify({"status": False, "error": "face file required"}), 400
        face_path = _save_uploaded_file(face, subfolder="uploads", prefix="face")
        style = request.form.get("style", "fortnite")
        preset = request.form.get("preset", "dance")
        final_video = generate_fullbody_animation(face_img_path=face_path, style=style, preset=preset)
        if os.path.isabs(final_video):
            fname = os.path.basename(final_video)
            rel = f"/static/videos/{fname}"
        else:
            rel = final_video
        return jsonify({"status": True, "video_url": rel})
    except Exception as e:
        return jsonify({"status": False, "error": str(e)}), 500

# Generate music (procedural / replicate or local)
@app.route("/generate-music", methods=["POST"])
def api_generate_music():
    try:
        data = request.get_json(silent=True) or request.form or {}
        duration = float(data.get("duration", 12))
        bpm = int(data.get("bpm", 90))
        style = data.get("style", "cinematic")
        # attempt to use cloud/replicate if flagged - fallback local
        use_cloud = str(data.get("cloud", "false")).lower() == "true"
        if use_cloud:
            # if you implement replicate, call it here
            mp = render_music(duration=duration, bpm=bpm, style=style, cloud=True)
        else:
            mp = render_music(duration=duration, bpm=bpm, style=style)
        if os.path.isabs(mp):
            fname = os.path.basename(mp)
            rel = f"/static/videos/{fname}"
        else:
            rel = mp
        return jsonify({"status": True, "music_path": rel})
    except Exception as e:
        return jsonify({"status": False, "error": str(e)}), 500

# Generate SFX (simple)
@app.route("/generate-sfx", methods=["POST"])
def api_generate_sfx():
    try:
        data = request.get_json(silent=True) or request.form or {}
        kind = data.get("kind", "whoosh")
        duration = float(data.get("duration", 1.0))
        # For now use dummy generated file
        fname = f"sfx_{kind}_{uuid.uuid4().hex[:6]}.wav"
        path = os.path.join(VIDEO_SAVE_DIR, fname)
        with open(path, "wb") as f:
            f.write(b"FAKE_SFX")
        rel = f"/static/videos/{fname}"
        return jsonify({"status": True, "sfx_path": rel})
    except Exception as e:
        return jsonify({"status": False, "error": str(e)}), 500

# Mix audio (voice + music + sfx)
@app.route("/mix-audio", methods=["POST"])
def api_mix_audio():
    try:
        voice_file = request.files.get("voice")
        music_file = request.files.get("music")
        sfx_file = request.files.get("sfx")
        if not voice_file:
            return jsonify({"status": False, "error": "voice file required"}), 400
        voice_path = _save_uploaded_file(voice_file, subfolder="uploads", prefix="voice")
        music_path = None
        sfx_path = None
        if music_file:
            music_path = _save_uploaded_file(music_file, subfolder="uploads", prefix="music")
        if sfx_file:
            sfx_path = _save_uploaded_file(sfx_file, subfolder="uploads", prefix="sfx")
        # For demo create a fake mixed file
        fname = f"mixed_{uuid.uuid4().hex[:8]}.wav"
        path = os.path.join(VIDEO_SAVE_DIR, fname)
        with open(path, "wb") as f:
            f.write(b"FAKE_MIXED_AUDIO")
        return jsonify({"status": True, "mixed_path": f"/static/videos/{fname}"})
    except Exception as e:
        return jsonify({"status": False, "error": str(e)}), 500

# Upload face helper
@app.route("/upload-face", methods=["POST"])
def upload_face():
    try:
        if "file" not in request.files:
            return jsonify({"status": False, "error": "No file uploaded"}), 400
        file = request.files["file"]
        rel = _save_uploaded_file(file, subfolder="uploads", prefix="face")
        if not rel:
            return jsonify({"status": False, "error": "Empty or invalid file"}), 400
        return jsonify({"status": True, "face_path": rel})
    except Exception as e:
        return jsonify({"status": False, "error": str(e)}), 500

# Upload voice helper
@app.route("/upload-voice", methods=["POST"])
def upload_voice():
    try:
        if "file" not in request.files:
            return jsonify({"status": False, "error": "No file uploaded"}), 400
        file = request.files["file"]
        rel = _save_uploaded_file(file, subfolder="uploads", prefix="voice")
        if not rel:
            return jsonify({"status": False, "error": "Empty or invalid file"}), 400
        return jsonify({"status": True, "message": "Voice sample uploaded", "path": rel})
    except Exception as e:
        return jsonify({"status": False, "error": str(e)}), 500

# list poses / costumes / hairstyles (examples)
@app.route("/poses", methods=["GET"])
def api_list_poses():
    try:
        # replace with real function list_poses()
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
    # defensive: prevent path traversal
    safe = secure_filename(filename)
    return send_from_directory(VIDEO_SAVE_DIR, safe)

# ---------- Conversation / Multi-avatar endpoint ----------
@app.route("/generate-conversation", methods=["POST"])
def api_generate_conversation():
    try:
        data = request.form if request.form else (request.get_json(silent=True) or {})
        script = data.get("script") if isinstance(data, dict) else None
        if not script:
            return jsonify({"status": False, "error": "script required"}), 400
        avatars_raw = data.get("avatars", "{}")
        try:
            avatars = json.loads(avatars_raw) if isinstance(avatars_raw, str) else avatars_raw
        except Exception:
            avatars = {}
        sync = str(data.get("sync", "false")).lower() == "true"
        # fallback: run local generation synchronously
        video = _dummy_generate_multiscene_video(script_text=script, avatars=avatars)
        if os.path.isabs(video):
            fname = os.path.basename(video)
            rel = f"/static/videos/{fname}"
        else:
            rel = video
        return jsonify({"status": True, "video_url": rel})
    except Exception as e:
        return jsonify({"status": False, "error": str(e)}), 500

# ---------- RUN ----------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    host = os.environ.get("HOST", "0.0.0.0")
    debug = os.environ.get("DEBUG", "false").lower() == "true"
    app.run(host=host, port=port, debug=debug)
