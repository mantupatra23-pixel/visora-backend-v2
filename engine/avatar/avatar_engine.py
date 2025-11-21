import random
from gtts import gTTS
from engine.facegen.face_generator import generate_face
from engine.fullbody.fullbody_engine import generate_fullbody_avatar
from engine.avatar.motion_engine import generate_motion_avatar
from engine.mixer.template_mixer import mix_avatar_with_template
from engine.voiceclone.clone_engine import clone_voice_and_generate
from engine.reel.reel_engine import generate_reel

def generate_talking_avatar(script_text, gender="any", emotion="neutral",
                            user_face=None, mode="fullbody"):

    # Generate avatar video first
    if user_face:
        face_img = user_face
    else:
        face_img = generate_face(gender)

    # Audio
if user_face:
    face_img = user_face
else:
    face_img = generate_face(gender)

# If cloned voice is provided
if os.path.exists("static/uploads/voice_sample.wav"):
    audio_path = clone_voice_and_generate(script_text, "static/uploads/voice_sample.wav")
else:
    audio_path = f"static/videos/voice_{random.randint(1000,9999)}.mp3"
    gTTS(script_text).save(audio_path)

# If cloned voice is provided
if os.path.exists("static/uploads/voice_sample.wav"):
    audio_path = clone_voice_and_generate(script_text, "static/uploads/voice_sample.wav")
else:
    audio_path = f"static/videos/voice_{random.randint(1000,9999)}.mp3"
    gTTS(script_text).save(audio_path)

    # Generate avatar
    if mode == "fullbody":
        avatar_video = generate_fullbody_avatar(face_img, audio_path, emotion)
    else:
        avatar_video = generate_motion_avatar(face_img, audio_path, emotion)

# After avatar video is created (avatar_video variable)
if apply_template:
    # cinematic template
    final_video = mix_avatar_with_template(avatar_video, bg_template, script_text)
elif mode == "reel":
    # auto reel editor
    final_video = generate_reel(avatar_video, script_text)
else:
    final_video = avatar_video

return final_video
