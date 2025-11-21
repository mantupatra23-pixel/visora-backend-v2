import replicate
import uuid
import os

def apply_outfit_change(face_image, outfit="suit"):
    """
    outfit options:
    - suit
    - hoodie
    - kurta
    - sherwani
    - blazer
    - saree
    - t-shirt
    - jacket
    """

    prompt = f"full upper-body portrait, wearing a {outfit}, studio lighting, photorealistic, clean fabric texture"

    output = replicate.run(
        "zjx1217/sdxl-outfit-manager:6d32b132eabfbf1e5b0ec0f44633cb6f45cabd3c5cb58e40400187f34ad6b4ec",
        input={
            "image": open(face_image, "rb"),
            "prompt": prompt,
            "preserve_pose": True,
            "preserve_face": True,
            "output_size": 1024
        }
    )

    out_url = output["image"]
    save_name = f"static/uploads/outfit_{uuid.uuid4().hex[:8]}.png"
    os.system(f"wget {out_url} -O {save_name}")

    return save_name
