import os
import uuid
from gtts import gTTS
from moviepy.editor import VideoFileClip, AudioFileClip, CompositeVideoClip, vfx
from .sd_api import generate_ai_background

from .template_engine import pick_template_bg, apply_template_style, build_captions

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def generate_cinematic_video(script_text, template="motivation"):

    video_id = str(uuid.uuid4())[:8]
    output_dir = "../static/videos"
    os.makedirs(output_dir, exist_ok=True)

    video_output = f"{output_dir}/{video_id}.mp4"
    audio_output = f"{output_dir}/{video_id}.mp3"

    # Voice
    tts = gTTS(script_text)
    tts.save(audio_output)
    audio = AudioFileClip(audio_output)

    # Background based on template
    bg_path = pick_template_bg(template)
    bg = VideoFileClip(bg_path).subclip(0, 10)

    # Apply template color grading + effect
    bg = apply_template_style(template, bg)

    # Captions
    subtitles = build_captions(script_text, bg.duration)

    # Combine
    final = CompositeVideoClip([bg, subtitles])
    final = final.set_audio(audio)

    # Export
    final.write_videofile(
        video_output,
        fps=24,
        codec="libx264",
        audio_codec="aac"
    )

    return video_output.replace("../static/", "/static/")
