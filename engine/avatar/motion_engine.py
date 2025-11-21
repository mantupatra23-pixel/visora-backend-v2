import replicate
import uuid
import os
from engine.avatar.emotion_engine import emotion_settings

def generate_motion_avatar(face_img, audio_file, emotion):
    model_id = "cjwbw/sadtalker:fa61f5c54e8bee53e103a73b411d09d8c7abf0fa1c2d7fcaadd41eabfff43c36"

    settings = emotion_settings(emotion)

    output = replicate.run(
        model_id,
        input={
            "source_image": open(face_img, "rb"),        # <-- USER FACE
            "driven_audio": open(audio_file, "rb"),
            "enhancer": settings["enhancer"],
            "expression_scale": settings["expression_scale"],
            "still_mode": settings["still_mode"],
            "preprocess": settings["preprocess"],
        }
    )

    video_url = output["output"][0]

    video_id = str(uuid.uuid4())[:8]
    save_path = f"static/videos/motion_{video_id}.mp4"

    os.system(f"wget {video_url} -O {save_path}")

    return save_path
