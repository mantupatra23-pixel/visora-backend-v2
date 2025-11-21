import random
import os
import uuid
from moviepy.editor import (
    VideoFileClip, AudioFileClip, CompositeVideoClip, TextClip, vfx
)

BROLL_DIR = "engine/reel/broll"
MUSIC_DIR = "engine/reel/music"

def pick_random_broll():
    files = [f for f in os.listdir(BROLL_DIR) if f.endswith(".mp4")]
    return os.path.join(BROLL_DIR, random.choice(files))

def pick_random_music():
    files = [f for f in os.listdir(MUSIC_DIR) if f.endswith(".mp3")]
    return os.path.join(MUSIC_DIR, random.choice(files))


def build_caption(text, duration, start):
    return TextClip(
        text,
        fontsize=70,
        color="white",
        stroke_color="black",
        stroke_width=3,
        method="caption",
        size=(950, None)
    ).set_position(("center", "bottom")).set_start(start).set_duration(duration)


def generate_reel(avatar_video_path, script_text):
    avatar = VideoFileClip(avatar_video_path).resize((1080, 1920))

    # Background b-roll scene
    broll = VideoFileClip(pick_random_broll()).subclip(0, avatar.duration)
    broll = broll.resize((1080, 1920)).fx(vfx.blur, 2).fx(vfx.colorx, 1.2)

    # Zoom effect on avatar
    avatar_zoom = avatar.fx(vfx.zoom_in, 1.05)

    # Build captions
    words = script_text.split()
    parts = [" ".join(words[i:i+5]) for i in range(0, len(words), 5)]

    caption_clips = []
    t = 0
    for chunk in parts:
        caption_clips.append(build_caption(chunk, 1.5, t))
        t += 1.5

    # Background music
    music = AudioFileClip(pick_random_music()).volumex(0.3)
    voice = avatar.audio.volumex(1.2)

    final_audio = music.set_duration(avatar.duration).audio_fadein(0.5).audio_fadeout(0.5)
    mixed_audio = CompositeVideoClip([]).set_audio(voice).set_audio(final_audio)

    final = CompositeVideoClip(
        [broll, avatar_zoom] + caption_clips
    ).set_duration(avatar.duration)

    final = final.set_audio(voice)

    out_id = str(uuid.uuid4())[:8]
    output_path = f"static/videos/reel_{out_id}.mp4"

    final.write_videofile(
        output_path,
        fps=24,
        codec="libx264",
        audio_codec="aac"
    )

    return output_path
