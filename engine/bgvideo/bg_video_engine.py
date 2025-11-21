import replicate
import uuid
import os

def generate_ai_background(prompt="cinematic background, bokeh lights, professional reel style"):
    """
    Generates a moving AI background video using Stable Video Diffusion
    """

    output = replicate.run(
        "stability-ai/stable-video-diffusion-img2vid:0a1ea16f7329d7b807c5966308b7de5d9e5c539f47e5c6dc3f65b45b27b4f02e",
        input={
            "prompt": prompt,
            "num_frames": 75,  
            "frames": 25,
            "video_length": 5,
            "aspect_ratio": "9:16",
            "motion_bucket_id": 70,
            "cfg_scale": 2.5,
            "fps": 24,
        }
    )

    # download video
    video_url = output["video"]
    save_path = f"static/videos/bg_{uuid.uuid4().hex[:8]}.mp4"

    os.system(f"wget {video_url} -O {save_path}")

    return save_path
