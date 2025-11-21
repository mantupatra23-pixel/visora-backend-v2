from flask import Flask, request, jsonify
import uuid
import os

from engine.video_engine import build_video
from engine.tts_engine import build_voice

app = Flask(__name__)

@app.get("/")
def home():
    return {"status": "Visora Backend V2 Running"}

@app.post("/generate")
def generate_video():
    data = request.json
    script = data.get("script")
    voice = data.get("voice", "female_hindi")
    resolution = data.get("resolution", "1080p")

    if not script:
        return jsonify({"error": "Script missing"}), 400

    job_id = str(uuid.uuid4())

    audio_file = build_voice(script, voice, job_id)
    video_file = build_video(script, audio_file, resolution, job_id)

    return jsonify({
        "job_id": job_id,
        "status": "completed",
        "video_url": f"/download/{job_id}"
    })
