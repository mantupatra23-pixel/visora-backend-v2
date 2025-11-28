# engines/tts_elevenlabs.py
"""
ElevenLabs TTS helper (simple).
Environment:
  ELEVENLABS_API_KEY  -> api key
Returns path to generated wav file.
"""
import os
import time
import requests
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger("tts")

OUT_DIR = Path(os.environ.get("VIDEO_SAVE_DIR", "static/videos"))
OUT_DIR.mkdir(parents=True, exist_ok=True)


def synthesize_voice(text: str, voice: str = "alloy", filename: str | None = None, api_key: str | None = None) -> str:
    """
    Calls ElevenLabs TTS (simple). Saves wav/mp3 and returns file path.
    Replace endpoint according to ElevenLabs docs if changed.
    """
    api_key = api_key or os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY missing in env")

    if not filename:
        filename = f"tts_{int(time.time())}.mp3"
    out_path = OUT_DIR / filename

    LOG.info("Synthesize TTS -> %s", out_path)

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg"
    }
    payload = {
        "text": text,
        # add other options if you have (voice settings)
    }

    resp = requests.post(url, json=payload, headers=headers, stream=True, timeout=60)
    if resp.status_code not in (200, 201):
        LOG.error("TTS failed: %s - %s", resp.status_code, resp.text)
        raise RuntimeError(f"TTS error: {resp.status_code}")

    with open(out_path, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                fh.write(chunk)

    return str(out_path)
