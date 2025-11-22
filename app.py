# app.py
import os
import uuid
import shutil
import subprocess
from pathlib import Path
from flask import Flask, request, jsonify, send_file
from gtts import gTTS
from PIL import Image, ImageDraw, ImageFont

app = Flask(__name__)

# Config
VIDEO_SAVE_DIR = os.environ.get("VIDEO_SAVE_DIR", "/tmp/videos")
IMAGE_SIZE = (1080, 1920)  # vertical 9:16
BG_COLOR = (18, 18, 18)
TEXT_COLOR = (255, 255, 255)
FONT_SIZE = 72
LINE_HEIGHT = 80

os.makedirs(VIDEO_SAVE_DIR, exist_ok=True)

def text_to_sentences(text):
    # Basic split by sentences (.,?,!) fallback
    if not text:
        return []
    import re
    parts = re.split(r'(?<=[\.\?\!])\s+', text.strip())
    parts = [p.strip() for p in parts if p.strip()]
    # if only 1 long part, try split by newline or commas
    if len(parts) == 1:
        if '\n' in parts[0]:
            parts = [p.strip() for p in parts[0].split('\n') if p.strip()]
        elif ',' in parts[0] and len(parts[0]) > 180:
            parts = [p.strip() for p in parts[0].split(',') if p.strip()]
    # cap number of slides to reasonable amount
    if len(parts) > 20:
        parts = parts[:20]
    return parts

def make_slide(text, out_path):
    img = Image.new("RGB", IMAGE_SIZE, color=BG_COLOR)
    draw = ImageDraw.Draw(img)
    # try to load a decent TTF, fallback to default
    font = None
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", FONT_SIZE)
    except Exception:
        font = ImageFont.load_default()
    # wrap text
    import textwrap
    max_width = 20  # tuned for default font; if using truetype will fit better
    if isinstance(font, ImageFont.FreeTypeFont):
        # compute wrap width by measuring
        max_px = IMAGE_SIZE[0] - 160
        words = text.split()
        lines = []
        cur = ""
        for w in words:
            test = (cur + " " + w).strip()
            w_px = draw.textsize(test, font=font)[0]
            if w_px <= max_px:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
    else:
        lines = textwrap.wrap(text, width=max_width)
    # compute vertical position
    total_h = len(lines) * LINE_HEIGHT
    y = (IMAGE_SIZE[1] - total_h) // 2
    for line in lines:
        w, h = draw.textsize(line, font=font)
        x = (IMAGE_SIZE[0] - w) // 2
        draw.text((x, y), line, font=font, fill=TEXT_COLOR)
        y += LINE_HEIGHT
    img.save(out_path)

def tts_save(text, out_mp3):
    tts = gTTS(text=text, lang="en")
    tts.save(out_mp3)

def get_audio_duration(path):
    # use ffprobe to get duration
    try:
        cmd = [
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration", "-of",
            "default=noprint_wrappers=1:nokey=1", path
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(res.stdout.strip())
    except Exception:
        return None

def make_video_from_images(image_files, durations, out_video):
    tmp_dir = os.path.dirname(out_video)
    # create short videos per image
    tmp_videos = []
    for idx, (img, dur) in enumerate(zip(image_files, durations)):
        tmp_mp4 = os.path.join(tmp_dir, f"seg_{idx:03d}.mp4")
        cmd = [
            "ffmpeg", "-y", "-loop", "1", "-i", img,
            "-c:v", "libx264", "-t", str(dur), "-pix_fmt", "yuv420p",
            "-vf", f"scale={IMAGE_SIZE[0]}:{IMAGE_SIZE[1]}", tmp_mp4
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        tmp_videos.append(tmp_mp4)
    # create concat file
    concat_txt = os.path.join(tmp_dir, "videos.txt")
    with open(concat_txt, "w") as f:
        for v in tmp_videos:
            f.write(f"file '{v}'\n")
    # concat
    cmd_concat = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_txt, "-c", "copy", out_video]
    subprocess.run(cmd_concat, check=True, capture_output=True)
    # cleanup tmp segment files
    for v in tmp_videos:
        os.remove(v)
    os.remove(concat_txt)

@app.route("/create-video", methods=["POST"])
def create_video():
    """
    POST form fields:
    - script : main text (required)
    - filename (optional)
    """
    script = request.form.get("script") or request.json.get("script") if request.is_json else None
    if not script or not script.strip():
        return jsonify({"error": "script required", "status": False}), 400

    job_id = uuid.uuid4().hex[:12]
    workdir = Path(VIDEO_SAVE_DIR) / job_id
    workdir.mkdir(parents=True, exist_ok=True)

    try:
        # 1) split into sentences/slides
        slides = text_to_sentences(script)
        if not slides:
            slides = [script.strip()]

        # 2) create images
        image_files = []
        for i, s in enumerate(slides):
            img_path = str(workdir / f"img_{i:03d}.png")
            make_slide(s, img_path)
            image_files.append(img_path)

        # 3) TTS -> single audio file
        audio_path = str(workdir / "audio.mp3")
        # use full script for audio (or join slides)
        tts_save(" ".join(slides), audio_path)

        # 4) get audio duration
        audio_duration = get_audio_duration(audio_path) or max(3 * len(image_files), 5)

        # 5) compute durations per slide
        per = audio_duration / len(image_files)
        durations = [per] * len(image_files)

        # 6) build video from images
        raw_video = str(workdir / "video_no_audio.mp4")
        final_video = str(workdir / (request.form.get("filename") or f"{job_id}.mp4"))
        make_video_from_images(image_files, durations, raw_video)

        # 7) merge audio
        cmd_merge = [
            "ffmpeg", "-y", "-i", raw_video, "-i", audio_path,
            "-c:v", "copy", "-c:a", "aac", "-shortest", final_video
        ]
        subprocess.run(cmd_merge, check=True, capture_output=True)

        # remove raw_video
        if os.path.exists(raw_video):
            os.remove(raw_video)

        return jsonify({"status": True, "video": str(final_video), "job_id": job_id})
    except subprocess.CalledProcessError as e:
        return jsonify({"status": False, "error": "ffmpeg failed", "details": e.stderr.decode() if e.stderr else str(e)}), 500
    except Exception as e:
        return jsonify({"status": False, "error": str(e)}), 500

@app.route("/download/<job_id>/<fname>", methods=["GET"])
def download(job_id, fname):
    path = Path(VIDEO_SAVE_DIR) / job_id / fname
    if not path.exists():
        return jsonify({"error": "file not found"}), 404
    return send_file(path, as_attachment=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))o

