# engine/character/hair_engine.py
import replicate, uuid, os
from dotenv import load_dotenv
load_dotenv()

HAIR_PRESETS = {
    "short": "short modern haircut, neat, photorealistic",
    "long": "long flowing hair, photorealistic",
    "manbun": "man bun hairstyle, neat",
    "curly": "curly hair, medium length",
    "anime": "anime style hair, big, stylized, colorful"
}

def list_hair_styles():
    return list(HAIR_PRESETS.keys())

def generate_hair_image(style_name):
    prompt = HAIR_PRESETS.get(style_name)
    if not prompt:
        raise ValueError("Unknown hair style")
    model = "stability-ai/stable-diffusion-xl"
    out = replicate.run(model, input={"prompt": prompt, "width":512, "height":512})
    img_url = out[0] if isinstance(out,list) else out
    out_path = f"static/uploads/hair_{style_name}_{uuid.uuid4().hex[:6]}.png"
    os.system(f"wget {img_url} -O {out_path}")
    return out_path
