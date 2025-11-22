#!/usr/bin/env python3
# app.py - Visora backend (Replicate-powered final)
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
import replicate

load_dotenv()

# ---------------- CONFIG ----------------
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
VIDEO_SAVE_DIR = os.environ.get("VIDEO_SAVE_DIR", os.path.join(BASE_DIR, "static", "videos"))
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.join(BASE_DIR, "static", "uploads"))
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "wav", "mp3", "mp4"}

os.makedirs(VIDEO_SAVE_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__, static_folder=os.path.join(BASE_DIR, "static"))

# Replicate config - from env
REPLICATE_API_TOKEN = os.environ.get("REPLICATE_API_TOKEN")
REPLICATE_MODEL_VIDEO = os.environ.get("REPLICATE_MODEL_VIDEO")  # cinematic default model
REPLICATE_MODEL_FULLBODY = os.environ.get("REPLICATE_MODEL_FULLBODY")
REPLICATE_MODEL_MULTISCENE10 = os.environ.get("REPLICATE_MODEL_MULTISCENE10")
REPLICATE_MODEL_TALKING_AVATAR = os.environ.get("REPLICATE_MODEL_TALKING_AVATAR")
REPLICATE_MODEL_MUSIC = os.environ.get("REPLICATE_MODEL_MUSIC")

if not REPLICATE_API_TOKEN:
    app.logger.warning("REPLICATE_API_TOKEN not set - video generation will fail until you add it to env.")

# -------------- UTIL ----------------
def allowed_file(filename):
    if not filename:
        return False
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _save_uploaded_file(file_obj, subfolder="uploads", prefix="file"):
    if not file_obj:
        return None
    fname = secure_filename(file_obj.filename or "")
    if not fname:
        return None
    ext = os.path.splitext(fname)[1] or ""
    file_id = uuid.uuid4().hex[-8:]
    save_name = f"{prefix}_{file_id}{ext}"
    dest_dir = os.path.join(BASE_DIR, "static", subfolder)
    os.makedirs(dest_dir, exist_ok=True)
    save_path = os.path.join(dest_dir, save_name)
    file_obj.save(save_path)
    return os.path.join("static", subfolder, save_name)

# ----------- REPLICATE GPU ENGINE (real video generator) ------>
REPLICATE_API_TOKEN = os.environ.get("REPLICATE_API_TOKEN")
REPLICATE_MODEL_VERSION = os.environ.get("REPLICATE_MODEL_VERSION", "minimax/hailuo-2.3")
POLL_INTERVAL = float(os.environ.get("REPLICATE_POLL_INTERVAL", 3))
POLL_TIMEOUT = int(os.environ.get("REPLICATE_POLL_TIMEOUT", 300))

def replicate_generate_video(prompt, timeout_seconds=POLL_TIMEOUT, **kwargs):
    """
    Create a video using Replicate predictions API
    Returns saved video file path OR None on failure
    """

    if not REPLICATE_API_TOKEN:
        app.logger.error("No REPLICATE_API_TOKEN found")
        return None

    # final model reference (no :version needed)
    model_ref = REPLICATE_MODEL_VERSION
    if not model_ref:
        app.logger.error("REPLICATE_MODEL_VERSION not configured")
        return None

    try:
        # Init client
        client = replicate.Client(api_token=REPLICATE_API_TOKEN)

        # Input payload
        payload = {
            "prompt": prompt,
        }
        payload.update(kwargs.get("input", {}))

        app.logger.info(f"Creating Replicate job for: {model_ref}")

        # Create prediction job
        prediction = client.predictions.create(
            model=model_ref,
            input=payload
        )

        start = time.time()

        # Poll status
        while True:
            prediction = client.predictions.get(prediction.id)
            status = prediction.status

            if status == "succeeded":
                break
            if status in ("failed", "canceled"):
                app.logger.error(f"Replicate failed: {status}")
                return None
            if time.time() - start > timeout_seconds:
                app.logger.error("Replicate job timeout")
                return None

            time.sleep(POLL_INTERVAL)

        # Get output
        output = getattr(prediction, "output", None)
        if not output:
            app.logger.error("No output from Replicate")
            return None

        # First item if list
        out_item = output[0] if isinstance(output, (list,tuple)) else output

        # extract URL
        if isinstance(out_item, dict):
            url = out_item.get("url") or out_item.get("uri")
        else:
            url = str(out_item)

        if not url:
            app.logger.error("No downloadable URL")
            return None

        # Download video
        dl = requests.get(url, stream=True, timeout=30)
        dl.raise_for_status()

        ext = ".mp4"
        ctype = dl.headers.get("content-type","")

        if "audio" in ctype:
            ext = ".mp3"
        elif "video" in ctype:
            if "quicktime" in ctype:
                ext = ".mov"
            else:
                ext = ".mp4"

        fname = f"replicate_{uuid.uuid4().hex[:8]}{ext}"
        save_path = os.path.join(BASE_DIR, VIDEO_SAVE_DIR, fname)
        os.makedirs(os.path.dirname(save_path), exist_ok=True)

        with open(save_path, "wb") as f:
            for chunk in dl.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        return save_path

    except Exception as e:
        app.logger.exception(f"Replicate exception: {e}")
        return None

# ---------------- Replicate real engine ----------------
def generate_replicate_video(script_text, max_scenes=1, timeout_seconds=300):
    """
    Generate a video using Replicate predictions.
    Returns absolute local path to saved mp4 or None on failure.
    """
    try:
        token = REPLICATE_API_TOKEN
        model = REPLICATE_MODEL_VIDEO
        if not token or not model:
            app.logger.error("REPLICATE_API_TOKEN or REPLICATE_MODEL_VIDEO not configured")
            return None

        client = replicate.Client(api_token=token)

        # create prediction
        prediction = client.predictions.create(
            model=model,
            input={
                "prompt": script_text,
                "max_scenes": max_scenes
            }
        )

        # wait for completion (blocks)
        prediction.wait()

        # get output URL (prediction.output might be list)
        out = prediction.output
        if isinstance(out, list) and len(out) > 0:
            out_item = out[0]
        else:
            out_item = out

        # if dict with url
        if isinstance(out_item, dict):
            url = out_item.get("url") or out_item.get("uri") or out_item.get("output") 
        else:
            url = str(out_item)

        if not url:
            app.logger.error("No usable download URL from replicate prediction")
            return None

        # download asset
        os.makedirs(VIDEO_SAVE_DIR, exist_ok=True)
        fname = f"replicate_{uuid.uuid4().hex[:8]}.mp4"
        save_path = os.path.join(VIDEO_SAVE_DIR, fname)

        dl = requests.get(url, stream=True, timeout=60)
        dl.raise_for_status()
        with open(save_path, "wb") as f:
            for chunk in dl.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        return save_path

    except Exception as e:
        app.logger.exception("Replicate video generate error: %s", e)
        return None

# --------------- ROUTES ----------------
@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "Visora Backend V2 Running Successfully", "status": True, "version": "2.0"})


@app.route("/test", methods=["GET"])
def test():
    return jsonify({"msg": "Backend test route working!"})

# ------------------ REAL GPU create-video ------------------
@app.route("/create-video", methods=["POST"])
def create_video():
    try:
        script = request.form.get("script", "")
        max_scenes = int(request.form.get("max_scenes", 1))

        if not script.strip():
            return jsonify({"status": False, "error": "Script is missing"}), 400

        app.logger.info("Trying Replicate GPU engine for video generation...")

        # Try real GPU engine
        video_path = generate_replicate_video(
            script_text=script,
            max_scenes=max_scenes
        )

        # Fallback
        if not video_path:
            app.logger.warning("GPU engine failed, using fallback dummy engine...")
            video_path = replicate_generate_video(script)

        if not video_path:
            return jsonify({"status": False, "error": "Video generation failed"}), 500

        fname = os.path.basename(video_path)
        rel = f"/static/videos/{fname}"

        return jsonify({
            "status": True,
            "message": "Video created successfully",
            "video_url": rel
        })

    except Exception as e:
        app.logger.exception("create_video error: %s", e)
        return jsonify({"status": False, "error": str(e)}), 500

# ---------------- 10-scene cinematic movie ----------------
@app.route("/generate-movie", methods=["POST"])
def generate_movie():
    try:
        data = request.form if request.form else (request.get_json(silent=True) or {})
        script = data.get("script", "") if isinstance(data, dict) else ""
        if not script:
            return jsonify({"status": False, "error": "script required"}), 400
        model = REPLICATE_MODEL_MULTISCENE10 or REPLICATE_MODEL_VIDEO
        if not model:
            return jsonify({"status": False, "error": "No replicate model configured for movie"}), 500
        extra = {"fps": 12, "width": 512, "height": 768, "motion": "cinematic", "max_scenes": 10}
        video_fullpath = replicate_generate_with_model(model, script, extra)
        if not video_fullpath:
            return jsonify({"status": False, "error": "movie generation failed"}), 500
        fname = os.path.basename(video_fullpath)
        return jsonify({"status": True, "movie_url": f"/static/videos/{fname}"})
    except Exception as e:
        app.logger.exception("generate_movie error: %s", e)
        return jsonify({"status": False, "error": str(e)}), 500


# ---------------- fullbody animation ----------------
@app.route("/generate-fullbody", methods=["POST"])
def generate_fullbody():
    try:
        data = request.form if request.form else (request.get_json(silent=True) or {})
        script = data.get("script", "") if isinstance(data, dict) else ""
        if not script:
            return jsonify({"status": False, "error": "script required"}), 400
        model = REPLICATE_MODEL_FULLBODY or REPLICATE_MODEL_VIDEO
        if not model:
            return jsonify({"status": False, "error": "No replicate model configured for fullbody"}), 500
        extra = {"fps": 12, "width": 512, "height": 768, "motion": "fullbody"}
        video_fullpath = replicate_generate_with_model(model, script, extra)
        if not video_fullpath:
            return jsonify({"status": False, "error": "fullbody generation failed"}), 500
        fname = os.path.basename(video_fullpath)
        return jsonify({"status": True, "video_url": f"/static/videos/{fname}"})
    except Exception as e:
        app.logger.exception("generate_fullbody error: %s", e)
        return jsonify({"status": False, "error": str(e)}), 500


# ---------------- fullbody quick motion endpoint ----------------
@app.route("/fullbody-motion", methods=["POST"])
def fullbody_motion():
    try:
        # expects 'face' file optionally and 'style'/'preset' fields
        face = request.files.get("face")
        face_path = None
        if face and allowed_file(face.filename):
            face_path = _save_uploaded_file(face, subfolder="uploads", prefix="face")
        data = request.form if request.form else (request.get_json(silent=True) or {})
        style = data.get("style", "fortnite") if isinstance(data, dict) else "fortnite"
        preset = data.get("preset", "dance") if isinstance(data, dict) else "dance"
        script = data.get("script", "") if isinstance(data, dict) else ""
        model = REPLICATE_MODEL_FULLBODY or REPLICATE_MODEL_VIDEO
        if not model:
            return jsonify({"status": False, "error": "No model configured"}), 500
        prompt = script or f"{style} {preset}"
        extra = {"motion": preset, "style": style}
        video_fullpath = replicate_generate_with_model(model, prompt, extra)
        if not video_fullpath:
            return jsonify({"status": False, "error": "fullbody motion failed"}), 500
        fname = os.path.basename(video_fullpath)
        return jsonify({"status": True, "video_url": f"/static/videos/{fname}"})
    except Exception as e:
        app.logger.exception("fullbody_motion error: %s", e)
        return jsonify({"status": False, "error": str(e)}), 500


# ---------------- talking avatar ----------------
@app.route("/talking-avatar", methods=["POST"])
def talking_avatar():
    try:
        data = request.form if request.form else (request.get_json(silent=True) or {})
        script = data.get("script", "") if isinstance(data, dict) else ""
        if not script:
            return jsonify({"status": False, "error": "script required"}), 400
        gender = data.get("gender", "any") if isinstance(data, dict) else "any"
        emotion = data.get("emotion", "neutral") if isinstance(data, dict) else "neutral"
        face_file = request.files.get("face")
        face_path = None
        if face_file and allowed_file(face_file.filename):
            face_path = _save_uploaded_file(face_file, subfolder="uploads", prefix="face")
        model = REPLICATE_MODEL_TALKING_AVATAR or REPLICATE_MODEL_VIDEO
        if not model:
            return jsonify({"status": False, "error": "No replicate model configured for talking avatar"}), 500
        prompt = script
        extra = {"gender": gender, "emotion": emotion}
        if face_path:
            extra["face_image"] = face_path  # if model supports it; adapt field name if needed
        video_fullpath = replicate_generate_with_model(model, prompt, extra)
        if not video_fullpath:
            return jsonify({"status": False, "error": "talking avatar generation failed"}), 500
        fname = os.path.basename(video_fullpath)
        return jsonify({"status": True, "video_url": f"/static/videos/{fname}"})
    except Exception as e:
        app.logger.exception("talking_avatar error: %s", e)
        return jsonify({"status": False, "error": str(e)}), 500


# ---------------- multiscene avatar (general) ----------------
@app.route("/multiscene-avatar", methods=["POST"])
def multiscene_avatar():
    try:
        data = request.form if request.form else (request.get_json(silent=True) or {})
        script = data.get("script", "") if isinstance(data, dict) else ""
        if not script:
            return jsonify({"status": False, "error": "script required"}), 400
        model = REPLICATE_MODEL_VIDEO
        if not model:
            return jsonify({"status": False, "error": "No replicate model configured"}), 500
        extra = {"motion": "multiscene"}
        video_fullpath = replicate_generate_with_model(model, script, extra)
        if not video_fullpath:
            return jsonify({"status": False, "error": "multiscene generation failed"}), 500
        fname = os.path.basename(video_fullpath)
        return jsonify({"status": True, "video_url": f"/static/videos/{fname}"})
    except Exception as e:
        app.logger.exception("multiscene_avatar error: %s", e)
        return jsonify({"status": False, "error": str(e)}), 500


# ---------------- generate music (if you have a replicate music model) ----------------
@app.route("/generate-music", methods=["POST"])
def api_generate_music():
    try:
        data = request.get_json(silent=True) or request.form or {}
        duration = float(data.get("duration", 12)) if isinstance(data, dict) else 12.0
        bpm = int(data.get("bpm", 90)) if isinstance(data, dict) else 90
        style = data.get("style", "cinematic") if isinstance(data, dict) else "cinematic"
        use_cloud = str(data.get("cloud", "false")).lower() == "true" if isinstance(data, dict) else False

        if not use_cloud or not REPLICATE_MODEL_MUSIC:
            return jsonify({"status": False, "error": "Music model not configured. Set REPLICATE_MODEL_MUSIC and send cloud=true"}), 501

        prompt = f"Generate {duration}s {style} music at {bpm} bpm"
        extra = {"duration": duration, "bpm": bpm, "style": style}
        out_path = replicate_generate_with_model(REPLICATE_MODEL_MUSIC, prompt, extra)
        if not out_path:
            return jsonify({"status": False, "error": "music generation failed"}), 500
        fname = os.path.basename(out_path)
        return jsonify({"status": True, "music_path": f"/static/videos/{fname}"})
    except Exception as e:
        app.logger.exception("generate_music error: %s", e)
        return jsonify({"status": False, "error": str(e)}), 500


# ---------------- generate sfx (if model) ----------------
@app.route("/generate-sfx", methods=["POST"])
def api_generate_sfx():
    try:
        data = request.get_json(silent=True) or request.form or {}
        kind = data.get("kind", "whoosh") if isinstance(data, dict) else "whoosh"
        # If you have a replicate SFX model, call it: else return 501
        sfx_model = os.environ.get("REPLICATE_MODEL_SFX")
        if not sfx_model:
            return jsonify({"status": False, "error": "No SFX model configured (set REPLICATE_MODEL_SFX)"}), 501
        prompt = f"Generate a short {kind} SFX"
        out_path = replicate_generate_with_model(sfx_model, prompt, {"kind": kind})
        if not out_path:
            return jsonify({"status": False, "error": "sfx generation failed"}), 500
        fname = os.path.basename(out_path)
        return jsonify({"status": True, "sfx_path": f"/static/videos/{fname}"})
    except Exception as e:
        app.logger.exception("generate_sfx error: %s", e)
        return jsonify({"status": False, "error": str(e)}), 500


# ---------------- Mix audio (voice + music + sfx) ----------------
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
        # For mixing, if you have a replicate audio mixing model, call it. Otherwise return path to voice (client can mix locally).
        mixing_model = os.environ.get("REPLICATE_MODEL_AUDIO_MIX")
        if mixing_model:
            prompt = "Mix voice with provided music and sfx"
            inputs = {"voice": voice_path}
            if music_path: inputs["music"] = music_path
            if sfx_path: inputs["sfx"] = sfx_path
            out = replicate_generate_with_model(mixing_model, prompt, inputs)
            if not out:
                return jsonify({"status": False, "error": "audio mixing failed"}), 500
            fname = os.path.basename(out)
            return jsonify({"status": True, "mixed_path": f"/static/videos/{fname}"})
        else:
            # no server mixing implemented -> return voice path so client can proceed
            return jsonify({"status": True, "voice_path": voice_path, "message": "No server-side mixing configured; returned voice file for client-side mixing"})
    except Exception as e:
        app.logger.exception("mix_audio error: %s", e)
        return jsonify({"status": False, "error": str(e)}), 500


# ---------------- Upload helpers ----------------
@app.route("/upload-face", methods=["POST"])
def upload_face():
    try:
        if "file" not in request.files:
            return jsonify({"status": False, "error": "No file part"}), 400
        file = request.files["file"]
        rel = _save_uploaded_file(file, subfolder="uploads", prefix="face")
        if not rel:
            return jsonify({"status": False, "error": "Empty file"}), 400
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
            return jsonify({"status": False, "error": "Empty file"}), 400
        return jsonify({"status": True, "message": "Voice saved", "path": rel})
    except Exception as e:
        app.logger.exception("upload_voice error: %s", e)
        return jsonify({"status": False, "error": str(e)}), 500


# ---------------- List helpers ----------------
@app.route("/poses", methods=["GET"])
def api_list_poses():
    poses = ["idle", "walk", "run", "dance", "action"]
    return jsonify({"status": True, "poses": poses})


@app.route("/costumes", methods=["GET"])
def api_list_costumes():
    costumes = ["casual", "formal", "armor", "fantasy"]
    return jsonify({"status": True, "costumes": costumes})


@app.route("/hairstyles", methods=["GET"])
def api_list_hairstyles():
    hair = ["short", "long", "ponytail", "mohawk"]
    return jsonify({"status": True, "hairstyles": hair})


# Serve saved videos
@app.route("/static/videos/<path:filename>", methods=["GET"])
def serve_video(filename):
    safe = secure_filename(filename)
    return send_from_directory(VIDEO_SAVE_DIR, safe)


# ---------------- Conversation / multi-avatar endpoint ----------------
@app.route("/generate-conversation", methods=["POST"])
def api_generate_conversation():
    try:
        data = request.form if request.form else (request.get_json(silent=True) or {})
        script = data.get("script", "") if isinstance(data, dict) else ""
        if not script:
            return jsonify({"status": False, "error": "script required"}), 400
        avatars_raw = data.get("avatars", "[]") if isinstance(data, dict) else "[]"
        try:
            avatars = json.loads(avatars_raw) if isinstance(avatars_raw, str) else avatars_raw
        except Exception:
            avatars = {}
        model = REPLICATE_MODEL_VIDEO
        if not model:
            return jsonify({"status": False, "error": "No replicate model configured"}), 500
        extra = {"mode": "conversation", "avatars": avatars}
        video_fullpath = replicate_generate_with_model(model, script, extra)
        if not video_fullpath:
            return jsonify({"status": False, "error": "conversation generation failed"}), 500
        fname = os.path.basename(video_fullpath)
        return jsonify({"status": True, "video_url": f"/static/videos/{fname}"})
    except Exception as e:
        app.logger.exception("generate_conversation error: %s", e)
        return jsonify({"status": False, "error": str(e)}), 500


# ---------------- RUN ----------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    host = os.environ.get("HOST", "0.0.0.0")
    debug = os.environ.get("DEBUG", "false").lower() == "true"
    app.run(host=host, port=port, debug=debug)
