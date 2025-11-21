# engine/character/fullbody_engine.py
import replicate
import uuid, os
from dotenv import load_dotenv
load_dotenv()
import time

REPLICATE = True
def generate_fullbody_animation(face_img_path, audio_path, pose="idle", style="realistic", outfit_image=None, hair_style=None):
    """
    Uses a cloud model (Replicate) to generate full-body animated video.
    face_img_path: path to user face image
    audio_path: path to voice/audio file
    pose: 'dance', 'walk', 'idle', 'hero', 'run', 'act'
    style: 'realistic','pixar','anime','fortnite'
    outfit_image: optional image path to guide outfit (from costume_engine)
    hair_style: optional descriptor or image path
    Returns saved mp4 path
    """
    model_id = "zjx1217/sadtalker-fullbody"  # replace with preferred fullbody model on replicate
    input_obj = {
        "source_image": open(face_img_path, "rb"),
        "driven_audio": open(audio_path, "rb"),
        "pose": pose,
        "style": style,
        "enhancer": "gfpgan",
        "preprocess": "full"
    }
    if outfit_image:
        input_obj["outfit_image"] = open(outfit_image, "rb")
    if hair_style:
        # allow passing hair_style text or image
        if os.path.exists(hair_style):
            input_obj["hair_image"] = open(hair_style, "rb")
        else:
            input_obj["hair_style"] = hair_style

    # run replicate (may take time)
    output = replicate.run(model_id, input=input_obj)
    # output expected to contain an URL to the video
    video_url = None
    if isinstance(output, dict) and "output" in output:
        # some models return dict with output list
        video_url = output["output"][0]
    elif isinstance(output, list):
        video_url = output[0]
    elif isinstance(output, str):
        video_url = output

    if not video_url:
        raise RuntimeError("No video output from model")

    out_fname = f"static/videos/fullbody_{uuid.uuid4().hex[:8]}.mp4"
    os.system(f"wget {video_url} -O {out_fname}")
    # small wait to ensure completion if needed
    time.sleep(1)
    return out_fname
