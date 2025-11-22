# app.py
"""
FastAPI service to create videos using Replicate predictions API.
Environment variables used:
  REPLICATE_API_TOKEN        -> your replicate API token (required)
  REPLICATE_MODEL_VERSION    -> model reference, e.g. "anotherjesse/zeroscope-v2-xl:9f747673" or "minimax/hailuo-2.3" (recommended include version if required)
  REPLICATE_POLL_INTERVAL    -> seconds between polls (default 3)
  REPLICATE_POLL_TIMEOUT     -> total seconds timeout for prediction (default 300)
  VIDEO_SAVE_DIR             -> directory to save downloaded videos (default "./videos")
"""

import os
import time
import uuid
import logging
from typing import Optional

import requests
from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Attempt to import replicate; if not installed, the app will error on startup
try:
    import replicate
except Exception as e:
    replicate = None

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("visora_replicate")

# Config from env
REPLICATE_API_TOKEN = os.environ.get("REPLICATE_API_TOKEN", "").strip()
REPLICATE_MODEL_VERSION = os.environ.get("REPLICATE_MODEL_VERSION", "").strip()
POLL_INTERVAL = int(os.environ.get("REPLICATE_POLL_INTERVAL", "3"))
POLL_TIMEOUT = int(os.environ.get("REPLICATE_POLL_TIMEOUT", "300"))
VIDEO_SAVE_DIR = os.environ.get("VIDEO_SAVE_DIR", "./videos")

os.makedirs(VIDEO_SAVE_DIR, exist_ok=True)

app = FastAPI(title="Visora Replicate Video API")

class CreateVideoResponse(BaseModel):
    status: bool
    message: str
    video_path: Optional[str] = None
    prediction_id: Optional[str] = None

def _validate_config():
    if not REPLICATE_API_TOKEN:
        raise RuntimeError("REPLICATE_API_TOKEN not set")
    if not REPLICATE_MODEL_VERSION:
        raise RuntimeError("REPLICATE_MODEL_VERSION not set")

def _init_client():
    if replicate is None:
        raise RuntimeError("replicate python package not installed")
    return replicate.Client(api_token=REPLICATE_API_TOKEN)

def _download_file(url: str, dest_dir: str, prefix: str = "replicate") -> str:
    """Download URL to dest_dir and return saved file path."""
    resp = requests.get(url, stream=True, timeout=30)
    resp.raise_for_status()

    # choose extension from content-type if possible
    ctype = resp.headers.get("content-type", "")
    ext = ".mp4"
    if "audio" in ctype and "mp3" in ctype:
        ext = ".mp3"
    elif "video" in ctype:
        if "quicktime" in ctype:
            ext = ".mov"
        else:
            ext = ".mp4"

    fname = f"{prefix}_{uuid.uuid4().hex[:8]}{ext}"
    save_path = os.path.join(dest_dir, fname)
    with open(save_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
    return save_path

@app.post("/create-video", response_model=CreateVideoResponse)
def create_video(
    script: str = Form(...),
    max_scenes: Optional[int] = Form(1),
):
    """
    Create a video using Replicate. Returns JSON with saved video path OR error.
    Example curl:
    curl -X POST "https://your-host/create-video" -F "script=Hello" -F "max_scenes=1"
    """
    try:
        _validate_config()
    except RuntimeError as e:
        logger.error("Config error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    try:
        client = _init_client()
    except Exception as e:
        logger.exception("Failed to init replicate client")
        raise HTTPException(status_code=500, detail="Replicate client init failed")

    # Build input payload for model - adapt fields to model API
    payload = {
        "prompt": script,
        "max_scenes": int(max_scenes),
    }

    # If users set model string as "owner/model:version" it's used directly
    model_ref = REPLICATE_MODEL_VERSION

    logger.info("Creating prediction for model=%s", model_ref)
    try:
        prediction = client.predictions.create(
            version=model_ref if ":" in model_ref or "/" in model_ref else model_ref,
            input=payload,
        )
    except replicate.exceptions.ReplicateError as e:
        logger.exception("Replicate API error")
        return JSONResponse(status_code=500, content={
            "status": False,
            "message": f"Replicate API error: {e}",
            "prediction_id": None,
            "video_path": None
        })
    except Exception as e:
        logger.exception("Unexpected error creating prediction")
        raise HTTPException(status_code=500, detail="Failed to create prediction")

    prediction_id = getattr(prediction, "id", None) or str(uuid.uuid4())
    logger.info("Prediction started id=%s", prediction_id)

    # Poll loop
    start_time = time.time()
    while True:
        try:
            prediction = client.predictions.get(prediction.id)
        except Exception as e:
            logger.exception("Failed to get prediction status")
            return JSONResponse(status_code=500, content={
                "status": False,
                "message": "Failed to fetch prediction status",
                "prediction_id": prediction_id,
                "video_path": None
            })
        status = getattr(prediction, "status", None)
        logger.debug("prediction status=%s", status)

        if status == "succeeded":
            break
        if status in ("failed", "canceled", "cancelled"):
            logger.error("Replicate job failed or canceled: %s", status)
            return JSONResponse(status_code=500, content={
                "status": False,
                "message": f"Replicate job failed: {status}",
                "prediction_id": prediction_id,
                "video_path": None
            })
        if time.time() - start_time > POLL_TIMEOUT:
            logger.error("Replicate job timed out")
            return JSONResponse(status_code=500, content={
                "status": False,
                "message": "Replicate job timed out",
                "prediction_id": prediction_id,
                "video_path": None
            })
        time.sleep(POLL_INTERVAL)

    # Get output field
    output = getattr(prediction, "output", None)
    if not output:
        logger.error("No output from replicate")
        return JSONResponse(status_code=500, content={
            "status": False,
            "message": "No output from replicate",
            "prediction_id": prediction_id,
            "video_path": None
        })

    # output may be list or single item
    out_item = output[0] if isinstance(output, (list, tuple)) else output
    # out_item might be dict with url or direct url string
    url = None
    if isinstance(out_item, dict):
        url = out_item.get("url") or out_item.get("uri")
    else:
        url = str(out_item)

    if not url:
        logger.error("No downloadable url in output")
        return JSONResponse(status_code=500, content={
            "status": False,
            "message": "No downloadable URL",
            "prediction_id": prediction_id,
            "video_path": None
        })

    # Download result
    try:
        saved = _download_file(url, VIDEO_SAVE_DIR, prefix="replicate_video")
    except Exception as e:
        logger.exception("Failed to download result")
        return JSONResponse(status_code=500, content={
            "status": False,
            "message": f"Failed to download result: {e}",
            "prediction_id": prediction_id,
            "video_path": None
        })

    logger.info("Saved video to %s", saved)
    return {
        "status": True,
        "message": "OK",
        "prediction_id": prediction_id,
        "video_path": saved
    }

# Health check
@app.get("/health")
def health():
    return {"status": "ok"}
