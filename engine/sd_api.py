import os
import requests
from PIL import Image
from io import BytesIO
import time
import uuid

# Replace with your API URL + TOKEN (Replicate/Stability/etc)
SD_API_URL = os.getenv("SD_API_URL", "")
SD_API_TOKEN = os.getenv("SD_API_TOKEN", "")

def generate_ai_background(prompt):
    if SD_API_URL == "" or SD_API_TOKEN == "":
        raise Exception("Stable Diffusion API URL or TOKEN missing!")

    headers = {
        "Authorization": f"Token {SD_API_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "prompt": prompt,
        "width": 1024,
        "height": 1536,
        "num_inference_steps": 30
    }

    # POST request — create SD job
    response = requests.post(SD_API_URL, json=payload, headers=headers)
    response.raise_for_status()
    result = response.json()

    # Some APIs return key → poll until image ready
    prediction_id = result.get("id", None)
    if not prediction_id:
        raise Exception("Invalid response: no prediction id")

    # Polling for result
    for _ in range(30):
        poll = requests.get(f"{SD_API_URL}/{prediction_id}", headers=headers)
        pdata = poll.json()

        # Check for image output (Replicate usually gives list of URLs)
        output = pdata.get("output", None)
        if output:
            image_url = output[0]
            img_bytes = requests.get(image_url).content
            img = Image.open(BytesIO(img_bytes)).convert("RGB")

            filename = f"sd_bg_{uuid.uuid4().hex}.png"
            out_path = os.path.join("engine/backgrounds", filename)
            img.save(out_path)

            return out_path

        time.sleep(2)

    raise Exception("Stable Diffusion generation timeout!")
