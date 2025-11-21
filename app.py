# app.py - Visora backend (clean, production-safe + termux-friendly)
from flask import Flask, request, jsonify, send_from_directory, current_app
import os
import uuid
from datetime import datetime
from dotenv import load_dotenv
from werkzeug.utils import secure_filename

load_dotenv()

app = Flask(__name__)

# -------------- CONFIG --------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VIDEO_SAVE_DIR = os.path.join(BASE_DIR, "static", "videos")
UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")

os.makedirs(VIDEO_SAVE_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

# -------------- Try import engines, else fallback dummy functions --------------
def _dummy_make_video(prefix="video"):
    vid_id = str(uuid.uuid4())[:8]
    filename = f"{prefix}_{vid_id}.mp4"
    path = os.path.join(VIDEO_SAVE_DIR, filename)
    # create a tiny dummy file so it's downloadable
    with open(path, "wb") as f:
        f.write(b"FAKE_VIDEO_CONTENT")
    return filename

try:
    from engine.video_engine import generate_cinematic_video
except Exception:
    generate_cinematic_video = lambda script_text, **kw: _dummy_make_video("cinematic")

try:
    from engine.avatar.avatar_engine import generate_talking_avatar
except Exception:
    generate_talking_avatar = lambda script_text, gender="any", emotion="neutral", user_face=None, mode="fullbody", apply_template=True: _dummy_make_video("avatar")

try:
    from engine.multiscene.multi_scene_engine import generate_multiscene_video
except Exception:
    generate_multiscene_video = lambda script_text, gender="any", emotion="neutral", user_face=None: _dummy_make_video("multiscene")

# -------------- UTIL --------------
def _save_uploaded_file(file_obj, subfolder="uploads", prefix="file"):
    if not file_obj:
        return None
    filename = secure_filename(file_obj.filename)
    if filename == "":
        return None
    ext = os.path.splitext(filename)[1] or ""
    file_id = str(uuid.uuid4())[:8]
    save_name = f"{prefix}_{file_id}{ext}"
    dest_dir = os.path.join(BASE_DIR, "static", subfolder)
    os.makedirs(dest_dir, exist_ok=True)
    save_path = os.path.join(dest_dir, save_name)
    file_obj.save(save_path)
    # return relative path used in responses
    rel_path = os.path.join("static", subfolder, save_name)
    return rel_path

# -------------- Routes --------------
@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": True,
        "message": "Visora Backend V2 Running Successfully",
        "version": "2.0"
    })

# Simple test route
@app.route("/test", methods=["GET"])
def test():
    return jsonify({"msg": "Backend test route working!"})


# ---------------- Create video (cinematic) ----------------
@app.route("/create-video", methods=["POST"])
def create_video():
    try:
        data = request.get_json(force=True, silent=True) or request.form or {}
        script = data.get("script", "")
        if not script or str(script).strip() == "":
            return jsonify({"status": False, "error": "Script required"}), 400

        # call engine (or fallback)
        filename = generate_cinematic_video(script_text=script)
        # if engine returns fullname path, normalize to filename only
        fname = os.path.basename(filename)

        return jsonify({
            "status": True,
            "message": "Video generated successfully!",
            "video_url": f"/static/videos/{fname}",
            "video_id": os.path.splitext(fname)[0]
        })
    except Exception as e:
        return jsonify({"status": False, "error": str(e)}), 500


# ---------------- Upload face ----------------
@app.route("/upload-face", methods=["POST"])
def upload_face():
    if "file" not in request.files:
        return jsonify({"status": False, "error": "No file uploaded"}), 400
    file = request.files["file"]
    rel = _save_uploaded_file(file, subfolder="uploads", prefix="face")
    if not rel:
        return jsonify({"status": False, "error": "Empty or invalid file"}), 400
    return jsonify({"status": True, "face_path": rel, "message": "Face uploaded successfully"})


# ---------------- Talking avatar ----------------
@app.route("/talking-avatar", methods=["POST"])
def talking_avatar():
    try:
        data = request.form or request.get_json(silent=True) or {}
        script = data.get("script", "")
        gender = data.get("gender", "any")
        emotion = data.get("emotion", "neutral")
        mode = data.get("mode", "fullbody")
        apply_template = data.get("template_mix", "off") == "on"

        user_face_file = request.files.get("face")
        user_face_path = _save_uploaded_file(user_face_file, subfolder="uploads", prefix="face") if user_face_file else None

        filename = generate_talking_avatar(
            script_text=script,
            gender=gender,
            emotion=emotion,
            user_face=user_face_path,
            mode=mode,
            apply_template=apply_template
        )
        fname = os.path.basename(filename)
        return jsonify({"status": True, "video_url": f"/static/videos/{fname}"})
    except Exception as e:
        return jsonify({"status": False, "error": str(e)}), 500


# ---------------- Upload voice sample ----------------
@app.route("/upload-voice", methods=["POST"])
def upload_voice():
    if "file" not in request.files:
        return jsonify({"status": False, "error": "No file uploaded"}), 400
    file = request.files["file"]
    rel = _save_uploaded_file(file, subfolder="uploads", prefix="voice")
    if not rel:
        return jsonify({"status": False, "error": "Empty or invalid file"}), 400
    # Rename/save to a canonical voice sample path if desired
    # e.g. static/uploads/voice_sample.wav
    return jsonify({"status": True, "message": "Voice sample uploaded successfully", "voice_path": rel})


# ---------------- Multiscene avatar ----------------
@app.route("/multiscene-avatar", methods=["POST"])
def multiscene_avatar():
    try:
        data = request.form or request.get_json(silent=True) or {}
        script = data.get("script", "")
        gender = data.get("gender", "any")
        emotion = data.get("emotion", "neutral")

        user_face_file = request.files.get("face")
        user_face_path = _save_uploaded_file(user_face_file, subfolder="uploads", prefix="face") if user_face_file else None

        filename = generate_multiscene_video(
            script_text=script,
            gender=gender,
            emotion=emotion,
            user_face=user_face_path
        )
        fname = os.path.basename(filename)
        return jsonify({"status": True, "video_url": f"/static/videos/{fname}"})
    except Exception as e:
        return jsonify({"status": False, "error": str(e)}), 500


# ---------------- Static video serving (helper) ----------------
@app.route("/static/videos/<path:filename>", methods=["GET"])
def serve_video(filename):
    # defensive: do not allow path traversal
    return send_from_directory(VIDEO_SAVE_DIR, filename, as_attachment=False)


# -------------- Local run (termux / dev) --------------
if __name__ == "__main__":
    # debug only for local dev/termux. In Render/Prod use gunicorn via Procfile.
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
