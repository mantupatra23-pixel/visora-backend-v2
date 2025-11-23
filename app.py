# app.py
"""
Visora - API server
- POST /create-video  -> enqueue job (returns job_uuid)
- GET  /job/<job_uuid> -> job metadata (status, video_url, logs)
- GET  /events/<job_uuid> -> SSE stream of job updates (progress/logs)
- GET  /static/videos/<filename> -> serve saved videos (optional)
Requires: REDIS_URL, HF_API_KEY, HF_MODEL, PUBLIC_BASE (optional), S3 config (optional)
"""
import os, time, uuid, json, logging
from flask import Flask, request, jsonify, Response, send_from_directory, abort
from flask_cors import CORS
from redis import Redis
from rq import Queue
from dotenv import load_dotenv

load_dotenv()

# ---------- CONFIG ----------
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
HF_API_KEY = os.environ.get("HF_API_KEY")  # Hugging Face token (router)
HF_MODEL = os.environ.get("HF_MODEL", "ali-vilab/text-to-video-ms-1.7b")
PUBLIC_BASE = os.environ.get("PUBLIC_BASE", "")  # e.g. https://your-app.onrender.com
VIDEO_DIR = os.environ.get("VIDEO_DIR", "static/videos")
VIDEO_DIR = os.path.abspath(VIDEO_DIR)
S3_ENABLED = bool(os.environ.get("S3_BUCKET"))
# ----------------------------

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("visora.api")

app = Flask(__name__, static_folder="static")
CORS(app)

# Redis + RQ
redis_conn = Redis.from_url(REDIS_URL)
q = Queue("default", connection=redis_conn)

# helper: redis keys
def job_key(job_uuid): return f"video:{job_uuid}"
def job_log_key(job_uuid): return f"video:{job_uuid}:logs"

def append_log(job_uuid, message):
    redis_conn.rpush(job_log_key(job_uuid), json.dumps({"ts": int(time.time()), "msg": message}))
    # keep last 500 entries
    redis_conn.ltrim(job_log_key(job_uuid), -500, -1)

def set_meta(job_uuid, mapping):
    redis_conn.hset(job_key(job_uuid), mapping=mapping)
    # publish update for SSE
    redis_conn.publish(f"video_updates:{job_uuid}", json.dumps({"ts":int(time.time()), "update": mapping}))

# Ensure video dir exists
os.makedirs(VIDEO_DIR, exist_ok=True)

# ---------- Endpoints ----------
@app.route("/create-video", methods=["POST"])
def create_video():
    if HF_API_KEY is None:
        return jsonify({"ok": False, "error": "HF_API_KEY not set"}), 500
    data = request.get_json(force=True, silent=True) or {}
    prompt = data.get("prompt") or data.get("inputs") or ""
    num_frames = int(data.get("num_frames", data.get("frames", 50)))
    params = data.get("parameters", {}) or {}
    if not prompt:
        return jsonify({"ok": False, "error": "missing prompt/inputs"}), 400

    job_uuid = uuid.uuid4().hex
    # initial metadata
    set_meta(job_uuid, {"status":"queued","created_at": str(int(time.time())), "prompt": prompt})
    append_log(job_uuid, "Job enqueued")

    # enqueue RQ task
    from tasks import process_video_job  # imported here to avoid circular import on module load
    rq_job = q.enqueue(process_video_job, job_uuid, prompt, num_frames, params, result_ttl=86400, timeout=3600)

    # store rq id
    set_meta(job_uuid, {"rq_id": rq_job.get_id()})
    return jsonify({"ok": True, "job_uuid": job_uuid, "rq_id": rq_job.get_id()}), 202

@app.route("/job/<job_uuid>", methods=["GET"])
def job_status(job_uuid):
    key = job_key(job_uuid)
    if not redis_conn.exists(key):
        return jsonify({"ok": False, "error": "not_found"}), 404
    meta = {k.decode(): v.decode() for k,v in redis_conn.hgetall(key).items()}
    # fetch recent logs
    logs = [json.loads(x) for x in redis_conn.lrange(job_log_key(job_uuid), 0, -1)]
    meta["logs"] = logs[-100:]
    return jsonify({"ok": True, "job": meta})

@app.route("/events/<job_uuid>")
def events(job_uuid):
    """SSE streaming of job updates. Uses Redis pub/sub; falls back to polling if pubsub unsupported."""
    if not redis_conn.exists(job_key(job_uuid)):
        return jsonify({"ok": False, "error": "not_found"}), 404

    pubsub = redis_conn.pubsub(ignore_subscribe_messages=True)
    channel = f"video_updates:{job_uuid}"
    pubsub.subscribe(channel)

    def gen():
        # send current state & logs first
        meta = {k.decode(): v.decode() for k,v in redis_conn.hgetall(job_key(job_uuid)).items()}
        yield f"data: {json.dumps({'type':'meta','meta':meta})}\n\n"
        logs = [json.loads(x) for x in redis_conn.lrange(job_log_key(job_uuid), -50, -1)]
        for entry in logs:
            yield f"data: {json.dumps({'type':'log','log':entry})}\n\n"
        # then stream pubsub messages
        try:
            for message in pubsub.listen():
                if message is None:
                    continue
                if message['type'] != 'message':
                    continue
                data = message['data']
                # message['data'] is bytes
                try:
                    payload = json.loads(data)
                except Exception:
                    payload = {"raw": data.decode() if isinstance(data, bytes) else str(data)}
                yield f"data: {json.dumps({'type':'update','update':payload})}\n\n"
                # if update includes status finished/failed -> break after sending
                if isinstance(payload.get("update"), dict) and payload["update"].get("status") in ("completed","failed","error"):
                    break
        finally:
            try:
                pubsub.unsubscribe(channel)
                pubsub.close()
            except Exception:
                pass

    return Response(gen(), mimetype="text/event-stream")

@app.route("/static/videos/<path:filename>")
def serve_video(filename):
    filepath = os.path.join(VIDEO_DIR, filename)
    if not os.path.exists(filepath):
        abort(404)
    return send_from_directory(VIDEO_DIR, filename, as_attachment=False)

# health
@app.route("/health")
def health():
    return jsonify({"ok": True, "time": int(time.time())})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
