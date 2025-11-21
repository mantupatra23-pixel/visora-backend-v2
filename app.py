from flask import Flask, request, jsonify, send_from_directory
import os
import uuid
from datetime import datetime
from engine.video_engine import generate_cinematic_video
from engine.avatar.avatar_engine import generate_talking_avatar
from dotenv import load_dotenv
load_dotenv()
from engine.multiscene.multi_scene_engine import generate_multiscene_video

app = Flask(__name__)

camera = data.get("camera", "auto")

# =====================================================
# GLOBAL CONFIGURATION
# =====================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VIDEO_SAVE_DIR = os.path.join(BASE_DIR, "static/videos")

# Auto-create folders
os.makedirs(VIDEO_SAVE_DIR, exist_ok=True)

# =====================================================
# BACKEND CHECK API
# =====================================================
@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": True,
        "message": "Visora Backend V2 Running Successfully",
        "version": "2.0"
    })

@app.route("/create-video", methods=["POST"])
def create_video():
    data = request.json
    script = data.get("script", "")

    if script.strip() == "":
        return jsonify({"status": False, "message": "Script required"}), 400

    try:
        video_url = generate_cinematic_video(script)
        return jsonify({
            "status": True,
            "message": "Video generated successfully!",
            "video_url": video_url
        })
    except Exception as e:
        return jsonify({"status": False, "error": str(e)})

@app.route("/upload-face", methods=["POST"])
def upload_face():
    if "file" not in request.files:
        return jsonify({"status": False, "error": "No file uploaded"}), 400

    file = request.files["file"]

    if file.filename == "":
        return jsonify({"status": False, "error": "Empty file"}), 400

    face_id = str(uuid.uuid4())[:8] + ".png"
    save_path = f"static/uploads/{face_id}"

    file.save(save_path)

    return jsonify({
        "status": True,
        "face_path": save_path,
        "message": "Face uploaded successfully"
    })

@app.route("/talking-avatar", methods=["POST"])
def talking_avatar():
    data = request.form
    script = data.get("script", "")
    gender = data.get("gender", "any")
    emotion = data.get("emotion", "neutral")
    mode = data.get("mode", "fullbody")   # NEW

    user_face = request.files.get("face")
    face_path = None
    if mode == "reel":
    apply_template = False

    if user_face:
        face_id = str(uuid.uuid4())[:8] + ".png"
        face_path = f"static/uploads/{face_id}"
        user_face.save(face_path)

    final_video = generate_talking_avatar(
        script_text=script,
        gender=gender,
        emotion=emotion,
        user_face=face_path,
        mode=mode
    )

    return jsonify({
        "status": True,
        "video_url": "/" + final_video
    })

@app.route("/talking-avatar", methods=["POST"])
def talking_avatar():
    data = request.form
    script = data.get("script", "")
    gender = data.get("gender", "any")
    emotion = data.get("emotion", "neutral")
    mode = data.get("mode", "fullbody")
    apply_template = data.get("template_mix", "off") == "on"

    user_face = request.files.get("face")
    face_path = None
    if user_face:
        face_id = str(uuid.uuid4())[:8] + ".png"
        face_path = f"static/uploads/{face_id}"
        user_face.save(face_path)

    final_video = generate_talking_avatar(
        script_text=script,
        gender=gender,
        emotion=emotion,
        user_face=face_path,
        mode=mode,
        apply_template=apply_template
    )

    return jsonify({
        "status": True,
        "video_url": "/" + final_video
    })

@app.route("/upload-voice", methods=["POST"])
def upload_voice():
    if "file" not in request.files:
        return jsonify({"status": False, "error": "No file uploaded"}), 400

    file = request.files["file"]

    save_path = "static/uploads/voice_sample.wav"
    file.save(save_path)

    return jsonify({
        "status": True,
        "message": "Voice sample uploaded successfully!"
    })

@app.route("/multiscene-avatar", methods=["POST"])
def multiscene_avatar():
    data = request.form
    script = data.get("script", "")
    gender = data.get("gender", "any")
    emotion = data.get("emotion", "neutral")

    user_face = request.files.get("face")
    face_path = None

    if user_face:
        face_id = str(uuid.uuid4())[:8] + ".png"
        face_path = f"static/uploads/{face_id}"
        user_face.save(face_path)

    final_video = generate_multiscene_video(
        script_text=script,
        gender=gender,
        emotion=emotion,
        user_face=face_path
    )

    return jsonify({
        "status": True,
        "video_url": "/" + final_video
    })

# =====================================================
# FIRST MAIN API
# /create-video (POST)
# Generates a fake sample MP4 file for now.
# Later will upgrade to full cinematic AI engine.
# =====================================================
@app.route("/create-video", methods=["POST"])
def create_video():
    try:
        data = request.get_json()

        # Get script
        script = data.get("script", "")
        if not script:
            return jsonify({"status": False, "error": "Script missing"}), 400

        # Unique file ID
        video_id = str(uuid.uuid4())[:8]
        filename = f"{video_id}.mp4"
        output_path = os.path.join(VIDEO_SAVE_DIR, filename)

        # Temporary dummy video data
        # (Later will integrate: MoviePy + TTS + Templates)
        with open(output_path, "wb") as f:
            f.write(b"FAKE_VIDEO_CONTENT")

        # Successful response
        return jsonify({
            "status": True,
            "message": "Video generated successfully!",
            "video_url": f"/static/videos/{filename}",
            "video_id": video_id
        })

    except Exception as e:
        return jsonify({"status": False, "error": str(e)}), 500


# =====================================================
# STATIC VIDEO FILE ACCESS
# Allows downloading/opening generated video
# =====================================================
@app.route("/static/videos/<path:filename>", methods=["GET"])
def serve_video(filename):
    return send_from_directory(VIDEO_SAVE_DIR, filename)


# =====================================================
# RUNNING THE BACKEND (TERMUX MODE)
# =====================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
