# tasks.py
"""
RQ worker task implementation.
Handles:
- call HF router (sync or returns job-id)
- poll if returned job-id / status_url
- download final mp4 (direct or via URL)
- save to static/videos and optionally upload to S3
- update Redis metadata and logs
"""
import os, time, json, logging, base64
from pathlib import Path
import requests
from redis import Redis
from botocore.exceptions import BotoCoreError, ClientError

# config (ENV)
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
HF_API_KEY = os.environ.get("HF_API_KEY")
HF_MODEL = os.environ.get("HF_MODEL", "ali-vilab/text-to-video-ms-1.7b")
HF_ROUTER_BASE = os.environ.get("HF_ROUTER_BASE", "https://router.huggingface.co/api/models")
VIDEO_DIR = Path(os.environ.get("VIDEO_DIR", "static/videos"))
VIDEO_DIR.mkdir(parents=True, exist_ok=True)
PUBLIC_BASE = os.environ.get("PUBLIC_BASE", "")  # if set, used to build absolute video_url
# S3 config optional
S3_BUCKET = os.environ.get("S3_BUCKET")
S3_KEY_PREFIX = os.environ.get("S3_KEY_PREFIX", "videos/")
AWS_REGION = os.environ.get("AWS_REGION")

# logging + redis
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("visora.tasks")
redis_conn = Redis.from_url(REDIS_URL)

# helpers
def job_key(job_uuid): return f"video:{job_uuid}"
def job_log_key(job_uuid): return f"video:{job_uuid}:logs"
def set_meta(job_uuid, mapping):
    redis_conn.hset(job_key(job_uuid), mapping=mapping)
    redis_conn.publish(f"video_updates:{job_uuid}", json.dumps({"ts": int(time.time()), "update": mapping}))
def append_log(job_uuid, msg):
    redis_conn.rpush(job_log_key(job_uuid), json.dumps({"ts": int(time.time()), "msg": msg}))
    redis_conn.ltrim(job_log_key(job_uuid), -500, -1)

def hf_router_url(model): return f"{HF_ROUTER_BASE}/{model}"

def try_download_url(url, headers=None, timeout=120):
    headers = headers or {}
    r = requests.get(url, headers=headers, stream=True, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"download failed {r.status_code} {r.text[:200]}")
    return r.content

def upload_to_s3(local_path: Path):
    if not S3_BUCKET:
        return None
    try:
        import boto3
        s3 = boto3.client("s3", region_name=AWS_REGION)
        key = f"{S3_KEY_PREFIX}{local_path.name}"
        s3.upload_file(str(local_path), S3_BUCKET, key, ExtraArgs={"ContentType":"video/mp4","ACL":"public-read"})
        # public url
        url = f"https://{S3_BUCKET}.s3.amazonaws.com/{key}"
        return url
    except Exception as e:
        log.exception("S3 upload failed: %s", e)
        return None

def save_bytes_to_file(job_uuid, bts):
    fname = f"video_{int(time.time())}_{job_uuid[:8]}.mp4"
    path = VIDEO_DIR / fname
    with open(path, "wb") as f:
        f.write(bts)
    return fname, path

def poll_hf_job(job_info: dict, timeout_seconds=900, poll_interval=6):
    """
    Try to poll until we find candidate urls in response.
    job_info may contain keys: job_id / id / status_url / output_url etc.
    """
    started = time.time()
    headers = {"Authorization": f"Bearer {HF_API_KEY}"}
    job_id = job_info.get("job_id") or job_info.get("id") or job_info.get("jid") or None
    status_url = job_info.get("status_url") or job_info.get("result_url") or job_info.get("output_url") or job_info.get("url")
    while time.time() - started < timeout_seconds:
        try:
            if status_url:
                r = requests.get(status_url, headers=headers, timeout=60)
            elif job_id:
                # try HF router jobs endpoint
                r = requests.get(f"{HF_ROUTER_BASE}/{HF_MODEL}/jobs/{job_id}", headers=headers, timeout=60)
            else:
                return {"error": "no_job_identifier"}

            # parse json
            try:
                j = r.json()
            except Exception:
                j = {"raw": r.text[:2000]}

            # find candidate urls
            candidate = []
            def find_urls(obj):
                if isinstance(obj, dict):
                    for v in obj.values(): find_urls(v)
                elif isinstance(obj, list):
                    for v in obj: find_urls(v)
                elif isinstance(obj, str):
                    if obj.startswith("http"):
                        candidate.append(obj)
            find_urls(j)
            if candidate:
                return {"urls": candidate, "raw": j}
            # check status field
            status = j.get("status") or j.get("state")
            if status and str(status).lower() in ("succeeded","finished","completed","success"):
                return {"raw": j}
        except Exception as e:
            log.debug("poll error: %s", e)
        time.sleep(poll_interval)
    return {"error": "timeout"}

def process_video_job(job_uuid: str, prompt: str, num_frames: int = 50, params: dict = None):
    append_log(job_uuid, "Worker started")
    set_meta(job_uuid, {"status":"running", "started_at": str(int(time.time()))})
    attempts = 0
    max_attempts = int(os.environ.get("JOB_MAX_ATTEMPTS","3"))
    headers = {"Authorization": f"Bearer {HF_API_KEY}", "x-wait-for-model": "true", "Content-Type":"application/json"}

    while attempts < max_attempts:
        attempts += 1
        try:
            payload = {"inputs": prompt, "parameters": {"num_frames": int(num_frames)}}
            if params: payload["parameters"].update(params)
            url = hf_router_url(HF_MODEL)
            append_log(job_uuid, f"Calling HF router attempt {attempts}")
            resp = requests.post(url, json=payload, headers=headers, stream=True, timeout=600)
            content_type = resp.headers.get("content-type","")
            # binary direct
            if resp.status_code == 200 and ("video" in content_type or "octet-stream" in content_type):
                bts = resp.content
                fname, path = save_bytes_to_file(job_uuid, bts)
                append_log(job_uuid, f"Saved binary to {fname}")
                # upload to S3 optional
                s3_url = upload_to_s3(path)
                video_url = s3_url if s3_url else (f"{PUBLIC_BASE}/static/videos/{fname}" if PUBLIC_BASE else f"/static/videos/{fname}")
                set_meta(job_uuid, {"status":"completed","filename": fname, "video_url": video_url, "attempts": str(attempts)})
                append_log(job_uuid, "Job completed successfully")
                return {"ok": True, "video_url": video_url}
            # try parse json
            try:
                j = resp.json()
            except Exception:
                j = {"raw": resp.text[:2000], "status_code": resp.status_code}
            append_log(job_uuid, f"Router returned JSON: keys={list(j.keys()) if isinstance(j, dict) else type(j)}")
            # look for immediate url(s)
            candidate = []
            def find_urls(obj):
                if isinstance(obj, dict):
                    for v in obj.values(): find_urls(v)
                elif isinstance(obj, list):
                    for v in obj: find_urls(v)
                elif isinstance(obj, str):
                    if obj.startswith("http"):
                        candidate.append(obj)
            find_urls(j)
            if candidate:
                # download first candidate
                append_log(job_uuid, f"Found candidate URL: {candidate[0]}")
                bts = try_download_url(candidate[0], headers=headers)
                fname, path = save_bytes_to_file(job_uuid, bts)
                s3_url = upload_to_s3(path)
                video_url = s3_url if s3_url else (f"{PUBLIC_BASE}/static/videos/{fname}" if PUBLIC_BASE else f"/static/videos/{fname}")
                set_meta(job_uuid, {"status":"completed","filename": fname, "video_url": video_url, "attempts": str(attempts)})
                append_log(job_uuid, "Job completed (downloaded candidate URL)")
                return {"ok": True, "video_url": video_url}
            # if job-id or status_url present -> poll
            if any(k in j for k in ("job_id","id","task_id","status_url","result_url","output_url")):
                append_log(job_uuid, "Detected job_id/status_url, entering poll loop")
                poll_res = poll_hf_job(j, timeout_seconds=int(os.environ.get("HF_POLL_TIMEOUT", "900")), poll_interval=int(os.environ.get("HF_POLL_INTERVAL","6")))
                if poll_res.get("urls"):
                    candidate = poll_res["urls"]
                    append_log(job_uuid, f"Poll found urls: {candidate[:3]}")
                    bts = try_download_url(candidate[0], headers=headers)
                    fname, path = save_bytes_to_file(job_uuid, bts)
                    s3_url = upload_to_s3(path)
                    video_url = s3_url if s3_url else (f"{PUBLIC_BASE}/static/videos/{fname}" if PUBLIC_BASE else f"/static/videos/{fname}")
                    set_meta(job_uuid, {"status":"completed","filename": fname, "video_url": video_url, "attempts": str(attempts)})
                    append_log(job_uuid, "Job completed after polling")
                    return {"ok": True, "video_url": video_url}
                else:
                    append_log(job_uuid, f"Poll did not find urls: {poll_res.get('error') or 'no urls'}")
            # else fallback
            append_log(job_uuid, f"Unexpected HF response: {str(j)[:400]}")
            set_meta(job_uuid, {"status":"error","error": str(j), "attempts": str(attempts)})
        except Exception as e:
            append_log(job_uuid, f"Attempt {attempts} exception: {e}")
            set_meta(job_uuid, {"status":"error","error": str(e), "attempts": str(attempts)})
        time.sleep(3 + attempts)
    # finished attempts -> mark failed
    set_meta(job_uuid, {"status":"failed","attempts": str(attempts)})
    append_log(job_uuid, "Job failed after retries")
    return {"ok": False, "error": "failed_after_retries"}
