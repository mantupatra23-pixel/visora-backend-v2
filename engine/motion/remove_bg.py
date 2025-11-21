import replicate
import uuid
import os

def remove_bg(video_path):
    output = replicate.run(
        "daanelson/rembg:cf7cc7e861dc...",
        input={"image": open(video_path, "rb")}
    )

    out_url = output["output"]
    masked = f"static/videos/fg_{uuid.uuid4().hex[:8]}.mp4"

    os.system(f"wget {out_url} -O {masked}")
    return masked
