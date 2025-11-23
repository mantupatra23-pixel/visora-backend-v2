# app.py
"""
HF-only video generation backend (final).
Features added:
 - input validation & limits
 - optional API_KEY auth for your API
 - background cleaner (remove old videos)
 - returns public download URL if SERVICE_BASE_URL is set
 - robust HF error reporting + router compatible
 - safe file handling & logging
"""
import os
import time
import uuid
import threading
import requests
import json
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_file, abort
from flask_cors import CORS

# ---------------- Config from env ----------------
HF_TOKEN = os.environ.get("HF_TOKEN", "").strip() or None
HF_MODEL = os.environ.get("HF_MODEL", "").strip() or None
HF_API_URL = os.environ.get("HF_API_URL", "").strip() or None  # optional override
SERVICE_BASE_URL = os.environ.get("SERVICE_BASE_URL", "").strip() or None
API_KEY = os.environ.get("API_KEY", "").strip() or None  # optional simple auth for your frontend

VIDEO_SAVE_DIR = os.environ.get("VIDEO_SAVE_DIR", "videos")
os.makedirs(VIDEO_SAVE_DIR, exist_ok=True)

# safety / limits
MAX_PROMPT_LENGTH = int(os.environ.get("MAX_PROMPT_LENGTH", 5000))
MAX_SCENES = int(os.environ.get("MAX_SCENES", 10))
MAX_VIDEO_AGE_DAYS = int(os.environ.get("MAX_VIDEO_AGE_DAYS", 7))
MAX_FILE_SIZE_MB = int(os.environ.get("MAX_FILE_SIZE_MB", 200))  # not strictly enforced for downloads

# worker / cleanup
WORKER_THREADS = int(os.environ.get("WORKER_THREADS", 1))

# Polls/timeouts (kept for compatibility; replicate removed)
REPLICATE_POLL_INTERVAL = float(os.environ.get("REPLICATE_POLL_INTERVAL", 3))
REPLICATE_POLL_TIMEOUT = int(os.environ.get("REPLICATE_POLL_TIMEOUT", 300))

# Construct HF_API_URL if not provided
if not HF_API_URL and HF_MODEL:
    HF_API_URL = f"https://api-inference.huggingface.co/models/{HF_MODEL}"

app = Flask(__name__)
CORS(app)


# ---------------- Helpers ----------------
def safe_json(resp):
    try:
        return resp.json()
    except Exception:
        return {"text": resp.text or ""}


def uuid_filename(prefix="hf_", ext="mp4"):
    return f"{prefix}{uuid.uuid4().hex[:12]}.{ext}"


def download_url_to_file(url, dest_path, timeout=120):
    """Download url to file path. Returns True on success, False otherwise."""
    try:
        r = requests.get(url, stream=True, timeout=timeout)
        r.raise_for_status()
        total = 0
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(8192):
                if chunk:
                    f.write(chunk)
                    total += len(chunk)
                    # optional - enforce size limit
                    if MAX_FILE_SIZE_MB and total > MAX_FILE_SIZE_MB * 1024 * 1024:
                        app.logger.warning("Download exceeded max file size, aborting.")
                        return False
        return True
    except Exception as e:
        app.logger.exception("Failed downloading asset: %s", e)
        return False


def file_public_url(fname):
    """Return public URL if SERVICE_BASE_URL is configured, else return local path."""
    if SERVICE_BASE_URL:
        return SERVICE_BASE_URL.rstrip("/") + f"/download?file={fname}"
    return os.path.join(VIDEO_SAVE_DIR, fname)


def cleanup_old_files():
    """Background job - delete old video files older than MAX_VIDEO_AGE_DAYS."""
    while True:
        try:
            cutoff = datetime.utcnow() - timedelta(days=MAX_VIDEO_AGE_DAYS)
            for fname in os.listdir(VIDEO_SAVE_DIR):
                fpath = os.path.join(VIDEO_SAVE_DIR, fname)
                try:
                    mtime = datetime.utcfromtimestamp(os.path.getmtime(fpath))
                    if mtime < cutoff:
                        os.remove(fpath)
                        app.logger.info("Removed old file %s", fpath)
                except Exception:
                    pass
        except Exception:
            app.logger.exception("Cleaner thread error")
        # run cleanup once per hour
        time.sleep(3600)


def check_api_key():
    """Simple API key check - use as decorator logic in endpoints if API_KEY is set."""
    if API_KEY:
        key = request.headers.get("X-API-KEY") or request.args.get("api_key") or request.form.get("api_key")
        if not key or key != API_KEY:
            abort(401, description="Invalid or missing API key")


# ---------------- Routes ----------------
@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": True,
        "hf_token_present": bool(HF_TOKEN),
        "hf_model": HF_MODEL or None,
        "service_base_url": SERVICE_BASE_URL,
        "video_save_dir": VIDEO_SAVE_DIR
    })


@app.route("/model-check", methods=["POST"])
def model_check():
    check_api_key()
    if not HF_TOKEN:
        return jsonify({"status": False, "error": "HF_TOKEN not set"}), 400
    model = (request.get_json(silent=True) or {}).get("model") or HF_MODEL
    if not model:
        return jsonify({"status": False, "error": "No model provided and no HF_MODEL set"}), 400

    url = os.environ.get("HF_API_URL") or f"https://api-inference.huggingface.co/models/{model}"
    try:
        headers = {"Authorization": f"Bearer {HF_TOKEN}"}
        r = requests.head(url, headers=headers, timeout=15)
        if r.status_code in (200, 204):
            return jsonify({"status": True, "model": model})
        else:
            return jsonify({"status": False, "http_status": r.status_code, "detail": safe_json(r)}), 400
    except Exception as e:
        app.logger.exception("Model check failed")
        return jsonify({"status": False, "error": str(e)}), 500


@app.route("/create-video", methods=["POST"])
def create_video():
    check_api_key()

    # accept JSON or form-data
    if request.is_json:
        data = request.get_json() or {}
    else:
        data = request.form.to_dict() or {}

    prompt = (data.get("script") or data.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"status": False, "error": "No script/prompt provided"}), 400
    if len(prompt) > MAX_PROMPT_LENGTH:
        return jsonify({"status": False, "error": "Prompt too long", "max": MAX_PROMPT_LENGTH}), 400

    try:
        max_scenes = int(data.get("max_scenes", 1))
    except Exception:
        max_scenes = 1
    max_scenes = max(1, min(max_scenes, MAX_SCENES))

    model = data.get("model") or HF_MODEL
    if not model:
        return jsonify({"status": False, "error": "No model configured (HF_MODEL)"}), 400

    hf_url = os.environ.get("HF_API_URL") or f"https://api-inference.huggingface.co/models/{model}"
    headers = {"Authorization": f"Bearer {HF_TOKEN}"} if HF_TOKEN else {}
    payload = {
        "inputs": prompt,
        "parameters": {"max_scenes": max_scenes}
    }

    try:
        resp = requests.post(hf_url, json=payload, headers=headers, timeout=180)
    except Exception as e:
        app.logger.exception("HF request failed")
        return jsonify({"status": False, "error": "HF request failed", "detail": str(e)}), 500

    if resp.status_code >= 300:
        # return HF response content to help debugging
        return jsonify({
            "status": False,
            "error": "HF error",
            "http_status": resp.status_code,
            "detail": safe_json(resp)
        }), 502

    result = safe_json(resp)

    # Interpret HF response: try to find a URL or video bytes
    video_url = None
    # If result is dict, search for common keys
    if isinstance(result, dict):
        # check common keys
        for k in ("generated_video", "video", "url", "output", "outputs"):
            if k in result:
                val = result.get(k)
                if isinstance(val, str) and val.startswith("http"):
                    video_url = val
                    break
                if isinstance(val, list) and val and isinstance(val[0], str) and val[0].startswith("http"):
                    video_url = val[0]
                    break
        # check outputs list of dicts
        outputs = result.get("outputs") or result.get("output")
        if not video_url and isinstance(outputs, list) and outputs:
            first = outputs[0]
            if isinstance(first, dict):
                video_url = first.get("url") or first.get("generated_video")
            elif isinstance(first, str) and first.startswith("http"):
                video_url = first
    elif isinstance(result, list) and result:
        first = result[0]
        if isinstance(first, dict):
            video_url = first.get("url") or first.get("generated_video")
        elif isinstance(first, str) and first.startswith("http"):
            video_url = first

    # If HF returned raw bytes content
    if not video_url and resp.headers.get("content-type", "").startswith("video/"):
        fname = uuid_filename()
        save_path = os.path.join(VIDEO_SAVE_DIR, fname)
        with open(save_path, "wb") as f:
            f.write(resp.content)
        return jsonify({"status": True, "file": file_public_url(fname)})

    if not video_url:
        return jsonify({"status": False, "error": "No video url returned by model", "detail": result}), 500

    # Download the video and return path/url
    fname = uuid_filename()
    save_path = os.path.join(VIDEO_SAVE_DIR, fname)
    ok = download_url_to_file(video_url, save_path, timeout=180)
    if not ok:
        return jsonify({"status": False, "error": "Failed to download video from model output", "video_url": video_url}), 500

    return jsonify({"status": True, "file": file_public_url(fname)})


@app.route("/download", methods=["GET"])
def download_file():
    # no API key required to download; frontend can use direct link
    fname = request.args.get("file")
    if not fname:
        return jsonify({"status": False, "error": "file param required"}), 400
    # prevent path traversal
    if "/" in fname or "\\" in fname:
        return jsonify({"status": False, "error": "invalid filename"}), 400
    path = os.path.join(VIDEO_SAVE_DIR, fname)
    if not os.path.exists(path):
        return jsonify({"status": False, "error": "file not found"}), 404
    return send_file(path, as_attachment=True)


@app.route("/predict", methods=["POST"])
def predict():
    check_api_key()
    if not HF_TOKEN:
        return jsonify({"status": False, "error": "HF_TOKEN not set"}), 400

    data = request.get_json(silent=True) or {}
    inputs = data.get("inputs")
    params = data.get("parameters", {})
    model = data.get("model") or HF_MODEL
    if not model:
        return jsonify({"status": False, "error": "No model specified"}), 400

    url = os.environ.get("HF_API_URL") or f"https://api-inference.huggingface.co/models/{model}"
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    body = {"inputs": inputs, "parameters": params}
    try:
        r = requests.post(url, json=body, headers=headers, timeout=180)
    except Exception as e:
        app.logger.exception("Predict failed")
        return jsonify({"status": False, "error": str(e)}), 500

    return jsonify({"status": r.status_code == 200, "http_status": r.status_code, "result": safe_json(r)}), (200 if r.status_code == 200 else 502)


# ---------------- Worker threads (cleanup etc.) ----------------
def worker_loop():
    while True:
        time.sleep(1)


if __name__ == "__main__":
    # start cleanup thread
    cleaner = threading.Thread(target=cleanup_old_files, daemon=True)
    cleaner.start()

    # start optional worker threads
    for i in range(max(1, WORKER_THREADS)):
        t = threading.Thread(target=worker_loop, daemon=True)
        t.start()

    # run
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
