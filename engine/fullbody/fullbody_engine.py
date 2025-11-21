import replicate
import uuid
import os
from engine.avatar.emotion_engine import emotion_settings

def generate_fullbody_avatar(face_img, audio_file, emotion):
    model = "zjx1217/sadtalker-fullbody"

    emotion_cfg = emotion_settings(emotion)

    output = replicate.run(
        model,
        input={
            "source_image": open(face_img, "rb"),
            "driven_audio": open(audio_file, "rb"),
            "expression_scale": emotion_cfg["expression_scale"],
            "still_mode": emotion_cfg["still_mode"],
            "preprocess": "full",
            "enhancer": "gfpgan",
            "pose_style": "normal",     # full body pose
            "size": 512                 # HD quality
        }
    )

    video_url = output["output"][0]

    video_id = str(uuid.uuid4())[:8]
    save_path = f"static/videos/fullbody_{video_id}.mp4"

    os.system(f"wget {video_url} -O {save_path}")

    return save_path
