import replicate
import uuid
import os
from base64 import b64decode

def generate_face(gender="any"):
    model = "stability-ai/sdxl"
    prompt = ""

    if gender == "male":
        prompt = "handsome young male portrait, 512x512, ultra realistic"
    elif gender == "female":
        prompt = "beautiful young female portrait, 512x512, ultra realistic"
    else:
        prompt = "realistic human portrait, 512x512"

    output = replicate.run(
        model,
        input={"prompt": prompt}
    )

    image_url = output[0]

    # Download image to backend
    img_id = str(uuid.uuid4())[:8]
    save_path = f"engine/avatars/auto_{img_id}.png"
    os.system(f"wget {image_url} -O {save_path}")

    return save_path
