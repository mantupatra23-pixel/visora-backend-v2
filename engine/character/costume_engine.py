# engine/character/costume_engine.py
import os, uuid, requests
import replicate
from dotenv import load_dotenv
load_dotenv()

COSTUME_PRESETS = {
    "suit": "photorealistic upper body portrait wearing a classic navy suit, collared shirt, tie, studio lighting",
    "hoodie": "casual hoodie style, streetwear, photorealistic",
    "kurta": "indian kurta, traditional, photorealistic, vibrant colour",
    "sherwani": "wedding sherwani, rich embroidery, photorealistic",
    "doctor": "doctor white coat and stethoscope, photorealistic",
    "police": "police uniform upper body portrait, photorealistic",
    "tshirt": "simple t-shirt, casual",
    "anime_kpop": "k-pop stage outfit, colorful, anime photoreal"
    # add more as needed (20+)
}

def list_costumes():
    return list(COSTUME_PRESETS.keys())

def generate_costume_image(preset_name):
    """
    Uses Replicate stable-diffusion style model to create an outfit guide image.
    Returns saved image path.
    """
    prompt = COSTUME_PRESETS.get(preset_name)
    if not prompt:
        raise ValueError("Unknown costume preset")
    model = "stability-ai/stable-diffusion-xl"  # placeholder; use your SDX model id
    output = replicate.run(model, input={"prompt": prompt, "width":512, "height":512, "samples":1})
    # output often an URL list
    img_url = output[0] if isinstance(output, list) else output
    out_path = f"static/uploads/outfit_{preset_name}_{uuid.uuid4().hex[:6]}.png"
    os.system(f"wget {img_url} -O {out_path}")
    return out_path
