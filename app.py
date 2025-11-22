import os, time, json, requests, uuid, base64

HUGGINGFACE_API_TOKEN = os.environ.get("HUGGINGFACE_API_TOKEN")
HUGGINGFACE_MODEL = os.environ.get("HUGGINGFACE_MODEL")  # e.g. "owner/model" or "spaces/owner/space-name"

HUGGINGFACE_POLL_INTERVAL = 3
HUGGINGFACE_POLL_TIMEOUT = 300
VIDEO_SAVE_DIR = os.environ.get("VIDEO_SAVE_DIR", "video_out")

def huggingface_generate_video(prompt, timeout_seconds=HUGGINGFACE_POLL_TIMEOUT, **kwargs):
    """
    Try call HF model. Returns saved video path or None.
    """
    if not HUGGINGFACE_API_TOKEN or not HUGGINGFACE_MODEL:
        app.logger.error("HF token or model not set")
        return None

    headers = {
        "Authorization": f"Bearer {HUGGINGFACE_API_TOKEN}",
        "Accept": "application/json"
    }

    payload = {"text": prompt}
    # merge additional field if model expects 'inputs' or 'prompt'
    payload.update(kwargs.get("input", {}))

    # If it's a Space with a /run endpoint, use: https://hf.space/embed/{owner}/{space}/api/predict/
    is_space = HUGGINGFACE_MODEL.startswith("spaces/") or "/spaces/" in HUGGINGFACE_MODEL

    try:
        if is_space:
            # Example for Spaces (some accept /api/predict or /run)
            # convert "spaces/owner/space" or "owner/space" into appropriate URL:
            parts = HUGGINGFACE_MODEL.replace("spaces/", "").split("/")
            owner, space = parts[0], parts[1] if len(parts) > 1 else parts[0]
            url = f"https://hf.space/embed/{owner}/{space}/api/predict/"
            r = requests.post(url, headers={"Authorization": f"Bearer {HUGGINGFACE_API_TOKEN}"}, json={"data":[prompt]})
        else:
            url = f"https://api-inference.huggingface.co/models/{HUGGINGFACE_MODEL}"
            r = requests.post(url, headers=headers, json=payload, stream=True, timeout=30)

        # handle errors
        if r.status_code >= 400:
            app.logger.error(f"HuggingFace error: {r.status_code} {r.text}")
            return None

        # If binary/video returned directly (some models), try to save
        content_type = r.headers.get("content-type", "")
        os.makedirs(VIDEO_SAVE_DIR, exist_ok=True)
        fname = f"hf_{uuid.uuid4().hex[:8]}.mp4"
        save_path = os.path.join(VIDEO_SAVE_DIR, fname)

        if "video" in content_type or "octet-stream" in content_type:
            with open(save_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            return save_path

        # otherwise parse as JSON (many spaces/models return JSON)
        data = r.json()
        # possible keys: 'data', 'output', 'url', 'result', 'video', 'blob'
        # check common patterns:
        candidate = None
        if isinstance(data, dict):
            # space prediction shape { "data": [...], "durations": ... }
            if data.get("data"):
                # find first item that looks like url/base64
                for it in data["data"]:
                    if isinstance(it, str) and it.startswith("http"):
                        candidate = it
                        break
                    if isinstance(it, str) and it.startswith("data:video"):
                        candidate = it
                        break
            for k in ("url","video","output","result"):
                v = data.get(k)
                if v:
                    if isinstance(v, str):
                        candidate = v
                        break
                    if isinstance(v, list) and v and isinstance(v[0], str):
                        candidate = v[0]
                        break

        # If candidate is data:base64 like data:video/mp4;base64,AAAA...
        if candidate and candidate.startswith("data:"):
            header, b64 = candidate.split(",",1)
            raw = base64.b64decode(b64)
            with open(save_path, "wb") as f:
                f.write(raw)
            return save_path

        # If candidate is an http url, attempt download
        if candidate and candidate.startswith("http"):
            dl = requests.get(candidate, stream=True, timeout=30)
            dl.raise_for_status()
            with open(save_path, "wb") as f:
                for chunk in dl.iter_content(8192):
                    if chunk:
                        f.write(chunk)
            return save_path

        app.logger.error(f"No usable video returned from HF model. Response keys: {list(data.keys()) if isinstance(data, dict) else type(data)}")
        return None

    except Exception as e:
        app.logger.exception(f"HuggingFace request failed: {e}")
        return None
