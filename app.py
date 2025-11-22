# app.py
# Simple Flask API to generate video using Replicate (preferred) or Hugging Face as fallback.
# Set env vars in Render: REPLICATE_API_TOKEN, REPLICATE_MODEL_VERSION, HF_TOKEN, HF_MODEL, VIDEO_SAVE_DIR

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import os, time, uuid, requests

# Config from env
REPLICATE_API_TOKEN = os.environ.get("REPLICATE_API_TOKEN", "").strip() or None
REPLICATE_MODEL_VERSION = os.environ.get("REPLICATE_MODEL_VERSION", "").strip() or None
HF_TOKEN = os.environ.get("HF_TOKEN", "").strip() or None
HF_MODEL = os.environ.get("HF_MODEL", "").strip() or None

VIDEO_SAVE_DIR = os.environ.get("VIDEO_SAVE_DIR", "videos")
os.makedirs(VIDEO_SAVE_DIR, exist_ok=True)

POLL_INTERVAL = float(os.environ.get("REPLICATE_POLL_INTERVAL", 3))
POLL_TIMEOUT = int(os.environ.get("REPLICATE_POLL_TIMEOUT", 300))

app = Flask(__name__)
CORS(app)

# ---------- Helpers ----------

def download_url_to_file(url, dest_path, timeout=30):
    try:
        r = requests.get(url, stream=True, timeout=timeout)
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(8192):
                if chunk:
                    f.write(chunk)
        return dest_path
    except Exception as e:
        app.logger.exception("Failed downloading asset: %s", e)
        return None

def replicate_create_and_wait(model_version, input_payload):
    """
    Create a prediction on Replicate via REST API and poll until completed.
    Returns list of outputs (may be dicts) or raises Exception.
    """
    headers = {"Authorization": f"Token {REPLICATE_API_TOKEN}", "Content-Type": "application/json"}
    create_url = "https://api.replicate.com/v1/predictions"
    body = {
        "version": model_version,
        "input": input_payload
    }
    # create
    resp = requests.post(create_url, json=body, headers=headers, timeout=30)
    if resp.status_code >= 300:
        raise RuntimeError(f"Replicate create failed: {resp.status_code} {resp.text}")
    pred = resp.json()
    pred_id = pred.get("id")
    if not pred_id:
        raise RuntimeError("No prediction id returned from replicate.")

    # poll
    start = time.time()
    get_url = f"{create_url}/{pred_id}"
    while True:
        r = requests.get(get_url, headers=headers, timeout=30)
        if r.status_code >= 400:
            raise RuntimeError(f"Replicate poll error: {r.status_code} {r.text}")
        data = r.json()
        status = data.get("status")
        app.logger.debug("Replicate status=%s", status)
        if status in ("succeeded", "failed", "canceled"):
            return data
        if time.time() - start > POLL_TIMEOUT:
            raise TimeoutError("Replicate job timed out")
        time.sleep(POLL_INTERVAL)

def call_hf_inference(model, token, payload):
    """
    Call Hugging Face Inference API: POST https://api-inference.huggingface.co/models/{model}
    Returns response JSON or binary depending on model.
    """
    url = f"https://api-inference.huggingface.co/models/{model}"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.post(url, headers=headers, json=payload, timeout=120)
    if resp.status_code >= 400:
        raise RuntimeError(f"HF inference error: {resp.status_code} {resp.text}")
    # try json
    try:
        return resp.json()
    except Exception:
        return resp.content

# ---------- Routes ----------

@app.route("/create-video", methods=["POST"])
def create_video():
    """
    Expects form fields:
    - script or prompt (text) (preferred key: 'script' or 'prompt')
    - optionally 'input' (json string), 'max_scenes', etc.
    """
    try:
        data = {}
        # support form-data or application/json
        if request.is_json:
            data = request.get_json()
        else:
            # form fields
            for k, v in request.form.items():
                data[k] = v

        prompt = data.get("script") or data.get("prompt") or data.get("text") or ""
        if not prompt:
            return jsonify({"error": "No prompt/script provided", "status": False}), 400

        # Build input payload for model
        model_input = {"prompt": prompt}
        # pass-through optional user-supplied input json (string) or values
        if "input" in data:
            # if JSON string, try parse
            try:
                import json
                parsed = json.loads(data["input"])
                if isinstance(parsed, dict):
                    model_input.update(parsed)
            except Exception:
                # not json, ignore
                pass

        # Try Replicate first
        if REPLICATE_API_TOKEN and REPLICATE_MODEL_VERSION:
            app.logger.info("Using Replicate model: %s", REPLICATE_MODEL_VERSION)
            rep_result = replicate_create_and_wait(REPLICATE_MODEL_VERSION, model_input)
            # rep_result may have 'output' field (list)
            outputs = rep_result.get("output")
            if not outputs:
                return jsonify({"error": "Replicate returned no output", "status": False}), 500

            # outputs could be list of dicts or URLs
            out_item = outputs[0] if isinstance(outputs, (list, tuple)) else outputs
            # If dict with 'url' or 'uri'
            download_url = None
            if isinstance(out_item, dict):
                download_url = out_item.get("url") or out_item.get("uri") or out_item.get("file") or None
            elif isinstance(out_item, str) and out_item.startswith("http"):
                download_url = out_item

            if download_url:
                fname = f"replicate_{uuid.uuid4().hex[:8]}.mp4"
                save_path = os.path.join(VIDEO_SAVE_DIR, fname)
                dl = download_url_to_file(download_url, save_path)
                if not dl:
                    return jsonify({"error": "Failed to download replicate output", "status": False}), 500
                return jsonify({"status": True, "file": save_path, "url": None})
            else:
                # return raw output
                return jsonify({"status": True, "output": outputs})

        # Fallback to Hugging Face
        elif HF_TOKEN and HF_MODEL:
            app.logger.info("Using Hugging Face model: %s", HF_MODEL)
            hf_resp = call_hf_inference(HF_MODEL, HF_TOKEN, model_input)
            # If HF returns url or bytes, handle accordingly
            # If hf_resp is dict with 'url' or 'outputs', try to download first url
            download_url = None
            if isinstance(hf_resp, dict):
                # look for obvious url fields
                if "url" in hf_resp:
                    download_url = hf_resp["url"]
                elif "outputs" in hf_resp and isinstance(hf_resp["outputs"], list) and hf_resp["outputs"]:
                    o = hf_resp["outputs"][0]
                    if isinstance(o, dict) and ("url" in o):
                        download_url = o["url"]
            elif isinstance(hf_resp, (bytes, bytearray)):
                # save bytes as file
                fname = f"hf_{uuid.uuid4().hex[:8]}.mp4"
                save_path = os.path.join(VIDEO_SAVE_DIR, fname)
                with open(save_path, "wb") as f:
                    f.write(hf_resp)
                return jsonify({"status": True, "file": save_path})

            if download_url:
                fname = f"hf_{uuid.uuid4().hex[:8]}.mp4"
                save_path = os.path.join(VIDEO_SAVE_DIR, fname)
                dl = download_url_to_file(download_url, save_path)
                if not dl:
                    return jsonify({"error": "Failed to download HF output", "status": False}), 500
                return jsonify({"status": True, "file": save_path})

            # otherwise return HF JSON response
            return jsonify({"status": True, "output": hf_resp})

        else:
            return jsonify({"error": "No backend configured. Set REPLICATE_API_TOKEN+REPLICATE_MODEL_VERSION or HF_TOKEN+HF_MODEL", "status": False}), 400

    except TimeoutError as te:
        app.logger.exception("Timeout: %s", te)
        return jsonify({"error": "Timeout waiting for job", "status": False}), 504
    except Exception as e:
        app.logger.exception("Create video failed: %s", e)
        return jsonify({"error": str(e), "status": False}), 500

@app.route("/video/<path:filename>", methods=["GET"])
def serve_video(filename):
    # serve saved video file if needed
    path = os.path.join(VIDEO_SAVE_DIR, filename)
    if os.path.exists(path):
        return send_file(path)
    return jsonify({"error": "file not found"}), 404

if __name__ == "__main__":
    # for local debug only
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
