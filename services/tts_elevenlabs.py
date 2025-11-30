import requests
import os

API = os.getenv("ELEVENLABS_API_KEY", "")

def elevenlabs_tts(text, out_path):
    if not API:
        raise RuntimeError("ELEVENLABS_API_KEY missing")

    url = "https://api.elevenlabs.io/v1/text-to-speech/alloy"
    headers = {"xi-api-key": API, "Content-Type": "application/json"}
    payload = {"text": text}

    r = requests.post(url, json=payload, headers=headers, stream=True)

    with open(out_path, "wb") as f:
        for chunk in r.iter_content(1024 * 16):
            if chunk:
                f.write(chunk)

    return out_path
