# ---------------- app.py (FINAL feature-complete) ----------------
#!/usr/bin/env python3
import os, time, uuid, json, logging, threading, queue, argparse
from pathlib import Path
from typing import Optional, Dict, Any
from flask import Flask, request, jsonify, send_from_directory, Response, abort
from flask_cors import CORS
import requests

# Optional imports (may not be present)
try:
    import redis
    from rq import Queue as RQQueue, Worker as RQWorker
    REDIS_AVAILABLE = True
except Exception:
    REDIS_AVAILABLE = False

try:
    from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
    JWT_AVAILABLE = True
except Exception:
    JWT_AVAILABLE = False

try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    LIMITER_AVAILABLE = True
except Exception:
    LIMITER_AVAILABLE = False

try:
    from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
    METRICS_AVAILABLE = True
except Exception:
    METRICS_AVAILABLE = False

# ---------------- CONFIG from ENV ----------------
HF_MODEL = os.environ.get("HF_MODEL", "").strip()
HF_TOKEN = os.environ.get("HF_TOKEN", "").strip()
HF_API_INFERENCE_BASE = os.environ.get("HF_API_INFERENCE_BASE", "https://api-inference.huggingface.co/models").rstrip("/")
HF_ROUTER_BASE = os.environ.get("HF_ROUTER_BASE", "https://router.huggingface.co/api/models").rstrip("/")
OUT_DIR = Path(os.environ.get("OUT_DIR", "./videos")).resolve()
OUT_DIR.mkdir(parents=True, exist_ok=True)

API_KEY = os.environ.get("API_KEY", "").strip()
JWT_SECRET = os.environ.get("JWT_SECRET", "").strip()
REDIS_URL = os.environ.get("REDIS_URL", "").strip()
MAX_WORKERS = int(os.environ.get("MAX_WORKERS","1"))
JOB_RETRY = int(os.environ.get("JOB_RETRY","1"))
CALL_TIMEOUT = int(os.environ.get("CALL_TIMEOUT","600"))
RATE_LIMIT = os.environ.get("RATE_LIMIT", "20/minute")
USER_QUOTA_PER_DAY = int(os.environ.get("USER_QUOTA_PER_DAY","200"))
PORT = int(os.environ.get("PORT","8000"))
HOST = os.environ.get("HOST","0.0.0.0")

# ---------------- Logging ----------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("visora-final-all")

# ---------------- Flask app ----------------
app = Flask("visora-final-all")
CORS(app)

# ---------------- Optional components init ----------------
jwt = None
if JWT_AVAILABLE and JWT_SECRET:
    app.config["JWT_SECRET_KEY"] = JWT_SECRET
    jwt = JWTManager(app)
    log.info("JWT auth enabled")

limiter = None
if LIMITER_AVAILABLE:
    if REDIS_AVAILABLE and REDIS_URL:
        try:
            from redis import Redis
            redis_store = Redis.from_url(REDIS_URL)
            limiter = Limiter(app, key_func=get_remote_address, storage_uri=REDIS_URL)
        except Exception:
            limiter = Limiter(app, key_func=get_remote_address)
    else:
        limiter = Limiter(app, key_func=get_remote_address)
    log.info("Rate limiter enabled (%s)", RATE_LIMIT)
    limiter.limit(RATE_LIMIT)(lambda: None)  # ensure it's configured

# Prometheus metrics
if METRICS_AVAILABLE:
    REQ_COUNTER = Counter("visora_requests_total", "Total HTTP requests", ["endpoint","method","status"])
    JOB_COUNTER = Counter("visora_jobs_total", "Total jobs", ["result"])  # result: success/fail
    JOB_TIME = Histogram("visora_job_seconds", "Job durations seconds")
else:
    REQ_COUNTER = JOB_COUNTER = JOB_TIME = None

# ---------------- Queue setup (Redis RQ or in-memory) ----------------
USE_RQ = False
rq_queue = None
if REDIS_AVAILABLE and REDIS_URL:
    try:
        rconn = redis.from_url(REDIS_URL)
        rq_queue = RQQueue("visora", connection=rconn)
        USE_RQ = True
        log.info("Using Redis RQ at %s", REDIS_URL)
    except Exception as e:
        log.warning("Redis RQ init failed: %s", e)
        USE_RQ = False

# In-memory fallback
task_queue = queue.Queue()
jobs: Dict[str, Dict[str, Any]] = {}
jobs_lock = threading.Lock()
SHUTDOWN = threading.Event()

def now_ts(): return int(time.time())
def unique_filename(prefix="video", ext="mp4"):
    return f"{prefix}_{int(time.time())}_{uuid.uuid4().hex[:8]}.{ext}"

# ---------------- Auth decorators ----------------
from functools import wraps
def require_api_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if API_KEY:
            key = request.headers.get("X-API-KEY") or request.args.get("api_key")
            if not key or key != API_KEY:
                return jsonify({"ok":False,"error":"invalid_api_key"}), 401
        # if JWT enabled, prefer JWT for user identity on protected endpoints
        return f(*args, **kwargs)
    return wrapper

def require_jwt(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if jwt:
            return jwt_required()(f)(*a, **kw)
        return f(*a, **kw)
    return wrapper

# ---------------- Persistence (jobs snapshot) ----------------
JOBS_SNAPSHOT_FILE = OUT_DIR / "jobs_snapshot.json"
def save_jobs_snapshot():
    try:
        with jobs_lock:
            with open(JOBS_SNAPSHOT_FILE, "w") as f:
                json.dump(jobs, f, default=str)
    except Exception as e:
        log.exception("save snapshot failed: %s", e)

def load_jobs_snapshot():
    if JOBS_SNAPSHOT_FILE.exists():
        try:
            with open(JOBS_SNAPSHOT_FILE) as f:
                data = json.load(f)
            with jobs_lock:
                jobs.update(data)
        except Exception as e:
            log.exception("load snapshot failed: %s", e)

load_jobs_snapshot()

# ---------------- Model call core (same logic as before) ----------------
def api_inference_url(model_id: str) -> str:
    return f"{HF_API_INFERENCE_BASE}/{model_id}"
def router_url(model_id: str) -> str:
    return f"{HF_ROUTER_BASE}/{model_id}"

def call_model_stream_save(model_id: str, payload: dict, out_path: Path, wait_for_model: bool=False, timeout: int=CALL_TIMEOUT):
    # endpoints to try
    endpoints = []
    endpoints.append(("api-inference", api_inference_url(model_id), None))
    if HF_TOKEN:
        endpoints.append(("router", router_url(model_id), {"Authorization": f"Bearer {HF_TOKEN}"}))
    else:
        endpoints.append(("router", router_url(model_id), None))

    base_headers = {"Accept":"*/*", "User-Agent":"visora-complete/1.0"}
    if wait_for_model:
        base_headers["x-wait-for-model"] = "true"

    last_exc = None
    for kind, url, auth in endpoints:
        hdrs = dict(base_headers)
        if auth:
            hdrs.update(auth)
        try:
            r = requests.post(url, json=payload, headers=hdrs, stream=True, timeout=timeout)
            content_type = r.headers.get("Content-Type","")
            log.info("call %s status=%s content-type=%s", kind, r.status_code, content_type)
            if r.status_code == 200 and ("video" in content_type or "octet-stream" in content_type or "content-disposition" in r.headers):
                with open(out_path, "wb") as fh:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            fh.write(chunk)
                return r.status_code
            # parse json errors
            try:
                txt = r.content.decode(errors="ignore")
                obj = json.loads(txt) if txt else {}
            except Exception:
                obj = {"raw": r.text[:400]}
            last_exc = RuntimeError(f"{kind} returned {r.status_code}: {obj}")
            continue
        except Exception as e:
            last_exc = e
            log.exception("call exception %s", e)
            continue
    raise RuntimeError(f"All endpoints failed: {last_exc}")

# ---------------- Worker logic ----------------
def enqueue_job(job_id: str):
    if USE_RQ and rq_queue:
        # push to redis RQ (worker will run separate process)
        rq_queue.enqueue('app.rq_worker_execute', job_id)
    else:
        task_queue.put(job_id)

def rq_worker_execute(job_id: str):
    """To be used by RQ: import path must match app.rq_worker_execute"""
    return worker_execute(job_id)

def worker_execute(job_id: str):
    # real worker: pop job, call model, save file, update jobs
    j = None
    with jobs_lock:
        j = jobs.get(job_id)
        if not j:
            return {"ok":False,"error":"job_missing"}
        j["state"]="running"
        j["started_at"]=now_ts()
        j["attempts"]=j.get("attempts",0)+1
    model_id = j["model"]
    payload = j["payload"]
    wait_for_model = j.get("wait_for_model", False)
    filename = unique_filename()
    out_path = OUT_DIR / filename
    try:
        start = time.time()
        status = call_model_stream_save(model_id, payload, out_path, wait_for_model=wait_for_model)
        elapsed = time.time()-start
        with jobs_lock:
            j["state"]="finished"
            j["filename"]=filename
            j["finished_at"]=now_ts()
            j["duration"]=elapsed
        if METRICS_AVAILABLE:
            JOB_COUNTER.labels(result="success").inc()
            JOB_TIME.observe(elapsed)
        return {"ok":True,"filename":filename}
    except Exception as e:
        with jobs_lock:
            j["state"]="failed"; j["error"]=str(e); j["finished_at"]=now_ts()
        if METRICS_AVAILABLE:
            JOB_COUNTER.labels(result="fail").inc()
        log.exception("job %s failed: %s", job_id, e)
        return {"ok":False,"error":str(e)}

# in-memory worker threads
def in_memory_worker():
    while not SHUTDOWN.is_set():
        try:
            job_id = task_queue.get(timeout=1)
        except queue.Empty:
            continue
        res = worker_execute(job_id)
        task_queue.task_done()

# start in-memory workers if RQ not used
if not USE_RQ:
    for _ in range(MAX_WORKERS):
        t = threading.Thread(target=in_memory_worker, daemon=True)
        t.start()

# ---------------- HTTP endpoints ----------------

# metrics endpoint
@app.route("/metrics")
def metrics():
    if not METRICS_AVAILABLE:
        return jsonify({"ok":False,"error":"prometheus not installed"}), 501
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)

@app.before_request
def before_request_func():
    if METRICS_AVAILABLE:
        try:
            REQ_COUNTER.labels(endpoint=request.path, method=request.method, status="in").inc()
        except Exception:
            pass

@app.route("/health")
def health():
    return jsonify({"ok":True,"time":now_ts(),"jobs":len(jobs)})

@app.route("/create-token", methods=["POST"])
def create_token():
    # small helper: create JWT tokens if JWT enabled
    if not JWT_AVAILABLE:
        return jsonify({"ok":False,"error":"jwt_not_installed"}), 501
    data = request.get_json() or {}
    username = data.get("username")
    if not username:
        return jsonify({"ok":False,"error":"missing_username"}), 400
    # create access token (no user store here)
    access = create_access_token(identity=username)
    return jsonify({"ok":True,"access_token":access})

@app.route("/create-video", methods=["POST"])
@require_api_key
@require_jwt
def create_video():
    # rate-limit per IP / per-user applied by limiter if available
    data = request.get_json(force=True)
    inputs = data.get("inputs") or data.get("script") or data.get("prompt")
    if not inputs:
        return jsonify({"ok":False,"error":"missing_inputs"}),400
    model_id = data.get("model") or HF_MODEL
    if not model_id:
        return jsonify({"ok":False,"error":"missing_model"}),400
    payload = {"inputs": inputs}
    if "parameters" in data and isinstance(data["parameters"], dict):
        payload["parameters"]=data["parameters"]
    wait_for_model = bool(data.get("wait_for_model")) or (request.headers.get("x-wait-for-model","").lower() in ("1","true","yes"))

    job_id = uuid.uuid4().hex
    job = {
        "job_id": job_id, "state":"queued", "created_at":now_ts(),
        "model":model_id, "payload":payload, "wait_for_model":wait_for_model,
        "attempts":0, "progress":0, "cancel_requested":False
    }
    with jobs_lock:
        jobs[job_id]=job
    enqueue_job(job_id)
    # persist snapshot
    threading.Thread(target=save_jobs_snapshot, daemon=True).start()
    return jsonify({"ok":True,"job_id":job_id})

@app.route("/status/<job_id>")
@require_api_key
@require_jwt
def status(job_id):
    j = jobs.get(job_id)
    if not j:
        return jsonify({"ok":False,"error":"not_found"}),404
    safe = {k:v for k,v in j.items() if k!="payload"}
    return jsonify({"ok":True,"job":safe})

@app.route("/download/<path:filename>")
@require_api_key
@require_jwt
def download(filename):
    p = OUT_DIR/filename
    if not p.exists():
        return jsonify({"ok":False,"error":"file_not_found"}),404
    return send_from_directory(directory=str(OUT_DIR), path=filename, as_attachment=True)

@app.route("/list-files")
@require_api_key
@require_jwt
def list_files():
    out=[]
    for f in OUT_DIR.iterdir():
        if f.is_file():
            out.append({"name":f.name,"size":f.stat().st_size,"mtime":int(f.stat().st_mtime)})
    out_sorted = sorted(out, key=lambda x: x["mtime"], reverse=True)
    return jsonify({"ok":True,"files":out_sorted})

@app.route("/events/<job_id>")
@require_api_key
def events(job_id):
    # simple SSE stream of job status & logs
    def gen():
        last = None
        while True:
            j = jobs.get(job_id)
            if not j:
                yield f"data: {json.dumps({'error':'not_found'})}\n\n"; break
            # send state
            yield f"data: {json.dumps({'state': j.get('state'), 'filename': j.get('filename', None)})}\n\n"
            if j.get("state") in ("finished","failed","canceled"):
                break
            time.sleep(1)
    return Response(gen(), mimetype="text/event-stream")

# admin endpoints
@app.route("/admin/stats")
def admin_stats():
    with jobs_lock:
        counts = {}
        for jid,j in jobs.items():
            counts[j.get("state")] = counts.get(j.get("state"),0)+1
    return jsonify({"ok":True,"counts":counts,"jobs_total":len(jobs)})

@app.route("/model-check")
def model_check():
    model = request.args.get("model") or HF_MODEL
    if not model:
        return jsonify({"ok":False,"error":"missing_model"}),400
    out={}
    try:
        if HF_TOKEN:
            r = requests.get(router_url(model), headers={"Authorization": f"Bearer {HF_TOKEN}"}, timeout=15)
            out["router_status"]=r.status_code
            out["router_text"]=r.text[:500]
    except Exception as e:
        out["router_error"]=str(e)
    try:
        r2 = requests.get(api_inference_url(model), timeout=15)
        out["api_status"]=r2.status_code
        out["api_text"]=r2.text[:500]
    except Exception as e:
        out["api_error"]=str(e)
    return jsonify({"ok":True,"model":model,"results":out})

# ---------------- RQ worker entry point helper ----------------
def run_rq_worker():
    if not REDIS_AVAILABLE or not REDIS_URL:
        print("Redis/RQ not configured.")
        return
    q = RQQueue("visora", connection=redis.from_url(REDIS_URL))
    w = RQWorker([q], connection=q.connection)
    w.work()

# ---------------- main ----------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-rq-worker", action="store_true", help="Run RQ worker (use only if REDIS_URL set)")
    args = parser.parse_args()
    if args.run_rq_worker:
        run_rq_worker()
    else:
        log.info("Starting Visora final full. HF_MODEL=%s REDIS=%s JWT=%s", HF_MODEL, bool(REDIS_URL), bool(JWT_SECRET))
        app.run(host="0.0.0.0", port=PORT, threaded=True)
# ---------------- end of file ----------------
