import random
import uuid
from moviepy.editor import VideoFileClip, TextClip, CompositeVideoClip, AudioFileClip, vfx

HOOKS = [
    "WAIT! LISTEN…",
    "STOP SCROLLING!",
    "YOU NEED THIS TODAY!",
    "SECRET REVEALED 💥",
    "MOST PEOPLE MISS THIS",
    "TRUTH YOU MUST HEAR",
    "DO NOT IGNORE THIS",
]

SFX_PATH = "engine/hooks/hook_sfx.mp3"  # Add small ding/whoosh sound


def generate_viral_hook():
    text = random.choice(HOOKS)

    # Background black video
    bg = VideoFileClip("engine/templates/cinematic_bg/1.mp4").subclip(0, 2)
    bg = bg.resize((1080, 1920)).fx(vfx.blur, 3)

    hook_text = TextClip(
        text,
        fontsize=110,
        font="Arial-Bold",
        color="white",
        stroke_color="black",
        stroke_width=4,
        method="caption",
        size=(1000, None)
    ).set_duration(2).set_position(("center", "center"))

    # Add sound effect
    if SFX_PATH:
        audio = AudioFileClip(SFX_PATH).volumex(1.4)
        bg = bg.set_audio(audio)

    # Combine
    final = CompositeVideoClip([bg, hook_text])
    out_id = str(uuid.uuid4())[:8]
    out_path = f"static/videos/hook_{out_id}.mp4"

    final.write_videofile(
        out_path,
        fps=24,
        codec="libx264",
        audio_codec="aac"
    )

    return out_path
