from moviepy.editor import *
import uuid
import os

VIDEO_DIR = "static/videos"
os.makedirs(VIDEO_DIR, exist_ok=True)

def generate_video(audio_path: str) -> str:
    # Black screen + audio
    audio = AudioFileClip(audio_path)

    clip = ColorClip(size=(1080, 1920), color=(0, 0, 0))
    clip = clip.set_duration(audio.duration)
    clip = clip.set_audio(audio)

    output_name = f"{uuid.uuid4()}.mp4"
    output_path = os.path.join(VIDEO_DIR, output_name)

    clip.write_videofile(output_path, fps=24, codec="libx264", audio_codec="aac")

    return output_path
