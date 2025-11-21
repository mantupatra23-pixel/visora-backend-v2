import os
import random
from gtts import gTTS

# ---- IMPORTS FIXED ----
from engine.facegen.face_generator import generate_face
from engine.fullbody.fullbody_engine import generate_fullbody_avatar
from engine.avatar.motion_engine import generate_motion_avatar
from engine.mixer.template_mixer import mix_avatar_with_template
from engine.voiceclone.clone_engine import clone_voice_and_generate
from engine.reel.reel_engine import generate_reel


def generate_talking_avatar(
        script_text,
        gender="any",
        emotion="normal",
        user_face=None,
        mode="fullbody",
        apply_template=False,
        bg_template=None
    ):
    
    # ------------------------------
    # 1) GENERATE FACE
    # ------------------------------
    if user_face:
        face_img = user_face
    else:
        face_img = generate_face(gender)

    # ------------------------------
    # 2) GENERATE/CLONE VOICE
    # ------------------------------
    voice_sample = "static/uploads/voice_sample.wav"

    if os.path.exists(voice_sample):
        audio_path = clone_voice_and_generate(script_text, voice_sample)
    else:
        rnd = random.randint(1000, 9999)
        audio_path = f"static/videos/voice_{rnd}.mp3"
        gTTS(script_text).save(audio_path)

    # ------------------------------
    # 3) GENERATE AVATAR VIDEO
    # ------------------------------
    if mode == "fullbody":
        avatar_video = generate_fullbody_avatar(face_img, audio_path)
    else:
        avatar_video = generate_motion_avatar(face_img, audio_path)

    # ------------------------------
    # 4) OPTIONAL TEMPLATE MIXING
    # ------------------------------
    if apply_template and bg_template:
        final_video = mix_avatar_with_template(avatar_video, bg_template)

    # ------------------------------
    # 5) AUTO REEL EDITOR
    # ------------------------------
    elif mode == "reel":
        final_video = generate_reel(avatar_video, script_text)

    # ------------------------------
    # 6) DEFAULT RETURN
    # ------------------------------
    else:
        final_video = avatar_video

    return final_video
