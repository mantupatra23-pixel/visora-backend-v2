# app.py
# Full HF-only backend with background job engine
# Requirements: Flask, flask-cors, requests
# Env vars:
#   HF_TOKEN       (required)
#   HF_MODEL       (optional default model slug)
#   VIDEO_SAVE_DIR (optional, default 'videos')
#   BASE_URL       (optional, used to build public download URLs)
#   HF_MAX_RETRIES (optional, default 3)
#   WORKER_THREADS (optional, default 2)

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import os, uuid, requests, time, json, logging, threading, queue
from pathlib import Path

# ---------- Config ----------
HF_TOKEN = os.environ.get("HF_TOKEN", "").strip() or None
HF_MODEL_DEFAULT = os.environ.get("HF_MODEL", "cerspense/zeroscope-v2-xl").strip()
VIDEO_SAVE_DIR = os.environ.get("VIDEO_SAVE_DIR", "videos")
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")
MAX_RETRIES = int(os.environ.get("HF_MAX_RETRIES", "3"))
WORKER_THREADS = int(os.environ.get("WORKER_THREADS", "2"))
JOB_RETENTION = int(os.environ.get("JOB_RETENTION", "200"))  # keep last N jobs in memory

os.makedirs(VIDEO_SAVE_DIR, exist_ok=True)

app = Flask(__name__)
CORS(app)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("hf-backend")

# ---------- Utils ----------
def uid():
    return uuid.uuid4().hex

def random_filename(prefix="hf", ext=".mp4"):
    return f"{prefix}_{uuid.uuid4().hex[:8]}{ext}"

def save_bytes_to_file(data_bytes, ext=".mp4"):
    fname = random_filename(ext=ext)
    path = os.path.join(VIDEO_SAVE_DIR, fname)
    with open(path, "wb") as f:
        f.write(data_bytes)
    return path

def safe_json(resp):
    try:
        return resp.json()
    except Exception:
        return None

def build_public_url(path):
    if not BASE_URL:
        return None
    filename = os.path.basename(path)
    return f"{BASE_URL}/download/{filename}"

def download_url_to_file(url, timeout=60):
    try:
        dl = requests.get(url, stream=True, timeout=timeout)
        dl.raise_for_status()
        ctype = dl.headers.get("content-type", "")
        ext = ".mp4" if "video" in ctype else (".mp3" if "audio" in ctype else Path(url).suffix or ".bin")
        data = dl.content
        path = save_bytes_to_file(data, ext=ext)
        return {"ok": True, "path": path}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def find_urls_in_json(obj):
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and (v.startswith("http://") or v.startswith("https://") or v.endswith(".mp4") or v.endswith(".mp3")):
                found.append(v)
            else:
                found += find_urls_in_json(v)
    elif isinstance(obj, list):
        for it in obj:
            found += find_urls_in_json(it)
    return found

# ---------- HF call & handling ----------
def call_hf_model_raw(model, prompt_or_input, parameters=None, timeout=120, retries=MAX_RETRIES):
    if not HF_TOKEN:
        return {"status": False, "error": "HF_TOKEN not configured"}
    api_url = f"https://api-inference.huggingface.co/models/{model}"
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    body = {"inputs": prompt_or_input}
    if parameters:
        body["parameters"] = parameters

    last_err = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(api_url, json=body, headers=headers, timeout=timeout)
        except Exception as e:
            last_err = str(e)
            logger.warning("HF request attempt %d failed: %s", attempt, e)
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            return {"status": False, "error": last_err}
        if resp.status_code >= 400:
            j = safe_json(resp)
            return {"status": False, "error": "HF error", "http_status": resp.status_code, "detail": j}
        return {"status": True, "response": resp}
    return {"status": False, "error": "Exceeded retries"}

def handle_hf_response(resp):
    ctype = resp.headers.get("content-type", "")
    # binary video/audio
    if "video" in ctype or "audio" in ctype or "octet-stream" in ctype:
        ext = ".mp4" if "video" in ctype else ".mp3" if "audio" in ctype else ".bin"
        path = save_bytes_to_file(resp.content, ext=ext)
        return {"status": True, "file": path, "content_type": ctype}
    # JSON
    j = safe_json(resp)
    if j is None:
        # unknown -> save raw
        path = save_bytes_to_file(resp.content, ext=".bin")
        return {"status": True, "file": path, "content_type": ctype}
    urls = find_urls_in_json(j)
    if urls:
        dl = download_url_to_file(urls[0])
        if dl["ok"]:
            return {"status": True, "file": dl["path"], "source_url": urls[0], "raw": j}
        else:
            return {"status": False, "error": f"Found URL but failed to download: {dl.get('error')}", "raw": j}
    return {"status": True, "result": j}

# ---------- In-memory job engine ----------
job_q = queue.Queue()
jobs = {}   # job_id -> job dict (simple in-memory)
jobs_order = []  # keep order for listing

def enqueue_job(payload):
    job_id = uid()
    job = {
        "id": job_id,
        "status": "queued",
        "created_at": time.time(),
        "updated_at": time.time(),
        "payload": payload,
        "result": None,
        "error": None,
    }
    jobs[job_id] = job
    jobs_order.append(job_id)
    # keep retention
    while len(jobs_order) > JOB_RETENTION:
        old = jobs_order.pop(0)
        jobs.pop(old, None)
    job_q.put(job_id)
    return job_id

def worker_loop(worker_idx):
    logger.info("Worker %d started", worker_idx)
    while True:
        job_id = job_q.get()
        if job_id is None:
            break
        job = jobs.get(job_id)
        if not job:
            job_q.task_done()
            continue
        job["status"] = "running"
        job["updated_at"] = time.time()
        try:
            payload = job["payload"]
            model = payload.get("model") or HF_MODEL_DEFAULT
            inp = payload.get("input") or payload.get("script") or payload.get("prompt") or ""
            params = payload.get("parameters") or payload.get("params") or {}
            call = call_hf_model_raw(model, inp, parameters=params)
            if not call.get("status"):
                job["status"] = "failed"
                job["error"] = call.get("error")
                job["updated_at"] = time.time()
                logger.error("Job %s failed: %s", job_id, job["error"])
                job_q.task_done()
                continue
            resp = call.get("response")
            handled = handle_hf_response(resp)
            if not handled.get("status"):
                job["status"] = "failed"
                job["error"] = handled.get("error")
                job["raw"] = handled.get("raw")
                job["updated_at"] = time.time()
                logger.error("Job %s failed after handle: %s", job_id, job["error"])
                job_q.task_done()
                continue
            # success
            job["status"] = "done"
            job["result"] = {}
            if "file" in handled:
                job["result"]["file"] = handled["file"]
                job["result"]["download_url"] = build_public_url(handled["file"])
            else:
                job["result"]["data"] = handled.get("result")
            job["updated_at"] = time.time()
            logger.info("Job %s done", job_id)
        except Exception as e:
            job["status"] = "failed"
            job["error"] = str(e)
            job["updated_at"] = time.time()
            logger.exception("Exception processing job %s", job_id)
        finally:
            job_q.task_done()

# start worker threads
for i in range(max(1, WORKER_THREADS)):
    t = threading.Thread(target=worker_loop, args=(i+1,), daemon=True)
    t.start()

# ---------- Routes ----------
@app.route("/create-video", methods=["POST"])
def create_video():
    # accept json or form-data
    if request.is_json:
        data = request.get_json() or {}
    else:
        data = request.form.to_dict() or {}

    prompt = data.get("script") or data.get("prompt")
    if not prompt:
        return jsonify({"status": False, "error": "No script/prompt provided"})

    model = HF_MODEL_DEFAULT
    if not HF_TOKEN:
        return jsonify({"status": False, "error": "HF_TOKEN missing"})

    # HuggingFace API call
    import requests, uuid, os, json

    HF_API_URL = f"https://router.huggingface.co/models/{model}"
    headers = {
        "Authorization": f"Bearer {HF_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {"inputs": prompt}

    try:
        r = requests.post(HF_API_URL, headers=headers, json=payload, timeout=300)
    except Exception as e:
        return jsonify({"status": False, "error": str(e)})

    if r.status_code >= 300:
        return jsonify({
            "status": False,
            "error": f"HF error {r.status_code}",
            "detail": r.text
        })

    resp = r.json()

    # Try multiple keys
    video_url = (
        resp.get("generated_video")
        or resp.get("video")
        or resp.get("output")
        or None
    )

    if not video_url:
        return jsonify({
            "status": False,
            "error": "No video URL returned",
            "raw_response": resp
        })

    # Save video locally
    try:
        fname = f"hf_{uuid.uuid4().hex[:8]}.mp4"
        save_path = os.path.join(VIDEO_SAVE_DIR, fname)

        vr = requests.get(video_url, stream=True, timeout=200)
        with open(save_path, "wb") as f:
            for chunk in vr.iter_content(8192):
                if chunk:
                    f.write(chunk)

        return jsonify({
            "status": True,
            "video_file": save_path,
            "video_url": video_url
        })
    except Exception as e:
        return jsonify({"status": False, "error": "Failed to save video", "detail": str(e)})

@app.route("/predict", methods=["POST"])
def predict():
    if request.is_json:
        data = request.get_json() or {}
    else:
        data = request.form.to_dict() or {}
    model = data.get("model") or HF_MODEL_DEFAULT
    raw_input = data.get("input") or data.get("script") or data.get("prompt") or ""
    parameters = None
    if "parameters" in data:
        try:
            parameters = json.loads(data["parameters"])
        except Exception:
            parameters = None
    parsed_input = raw_input
    if isinstance(raw_input, str):
        s = raw_input.strip()
        if s.startswith("{") or s.startswith("["):
            try:
                parsed_input = json.loads(s)
            except Exception:
                parsed_input = raw_input
    call = call_hf_model_raw(model, parsed_input, parameters=parameters)
    if not call.get("status"):
        return jsonify({"status": False, "error": call.get("error"), "detail": call.get("detail", None)}), 500
    resp = call.get("response")
    handled = handle_hf_response(resp)
    if not handled.get("status"):
        return jsonify({"status": False, "error": handled.get("error"), "raw": handled.get("raw", None)}), 500
    out = {"status": True}
    if "file" in handled:
        out["file"] = handled["file"]
        if BASE_URL:
            out["download_url"] = build_public_url(handled["file"])
    else:
        out["result"] = handled.get("result")
    return jsonify(out)

@app.route("/create-video", methods=["POST"])
def create_video():
    if request.is_json:
        payload = request.get_json() or {}
    else:
        payload = request.form.to_dict() or {}
    prompt = (payload.get("script") or payload.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "No prompt/script provided", "status": False}), 400
    params = {}
    if "max_scenes" in payload:
        try:
            params["max_scenes"] = int(payload.get("max_scenes"))
        except Exception:
            pass
    if "input" in payload:
        try:
            parsed = json.loads(payload["input"])
            params.setdefault("input", {})
            if isinstance(parsed, dict):
                params["input"].update(parsed)
            else:
                params["input"] = parsed
        except Exception:
            params.setdefault("input", payload["input"])
    call = call_hf_model_raw(HF_MODEL_DEFAULT, prompt, parameters=params)
    if not call.get("status"):
        return jsonify({"status": False, "error": call.get("error"), "detail": call.get("detail")}), 500
    resp = call.get("response")
    handled = handle_hf_response(resp)
    if not handled.get("status"):
        return jsonify({"status": False, "error": handled.get("error"), "raw": handled.get("raw", None)}), 500
    out = {"status": True}
    if "file" in handled:
        out["file"] = handled["file"]
        if BASE_URL:
            out["download_url"] = build_public_url(handled["file"])
    else:
        out["result"] = handled.get("result")
    return jsonify(out)

@app.route("/enqueue", methods=["POST"])
def enqueue():
    if request.is_json:
        payload = request.get_json() or {}
    else:
        payload = request.form.to_dict() or {}
    if not payload:
        return jsonify({"status": False, "error": "No payload provided"}), 400
    job_id = enqueue_job(payload)
    return jsonify({"status": True, "job_id": job_id})

@app.route("/status/<job_id>", methods=["GET"])
def job_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"status": False, "error": "Job not found"}), 404
    return jsonify(job)

@app.route("/jobs", methods=["GET"])
def list_jobs():
    out = []
    for jid in reversed(jobs_order[-100:]):
        j = jobs.get(jid)
        if j:
            out.append({"id": j["id"], "status": j["status"], "created_at": j["created_at"], "updated_at": j["updated_at"]})
    return jsonify({"status": True, "jobs": out})

@app.route("/cancel/<job_id>", methods=["POST"])
def cancel_job(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"status": False, "error": "Job not found"}), 404
    if job["status"] in ("done", "failed"):
        return jsonify({"status": False, "error": "Cannot cancel finished job"}), 400
    # Simple cancellation: mark as canceled; worker will check status only at start of job
    job["status"] = "canceled"
    job["updated_at"] = time.time()
    return jsonify({"status": True, "id": job_id, "new_status": "canceled"})

@app.route("/download/<filename>", methods=["GET"])
def download_file(filename):
    path = os.path.join(VIDEO_SAVE_DIR, filename)
    if not os.path.isfile(path):
        return jsonify({"status": False, "error": "File not found"}), 404
    return send_file(path, as_attachment=True)

# ---------- Run ----------
if __name__ == "__main__":
    if not HF_TOKEN:
        logger.warning("HF_TOKEN is not configured. Model calls will fail.")
    port = int(os.environ.get("PORT", "8000"))
    logger.info("Starting HF backend on port %s with %d workers", port, WORKER_THREADS)
    app.run(host="0.0.0.0", port=port)
