import uuid
from moviepy.editor import VideoFileClip, concatenate_videoclips, vfx

from engine.avatar.avatar_engine import generate_talking_avatar
from engine.reel.reel_engine import generate_reel
from engine.camera.camera_motion import apply_camera_motion

def split_script(script_text, parts=3):
    words = script_text.split()
    chunk_size = len(words) // parts

    scenes = []

    for i in range(parts):
        start = i * chunk_size
        end = (i + 1) * chunk_size if i < parts - 1 else len(words)
        scenes.append(" ".join(words[start:end]))
    return scenes


def generate_multiscene_video(script_text, gender, emotion, user_face=None):
    scenes = split_script(script_text, parts=3)
    scene_clips = []

    # Scene 1: FULLBODY Avatar
    scene1 = generate_talking_avatar(
        scenes[0], gender, emotion, user_face=user_face, mode="fullbody"
    )
    scene_clips.append(VideoFileClip(scene1).fx(vfx.fadein, 0.4))

    # Scene 2: HEADSHOT Avatar
    scene2 = generate_talking_avatar(
        scenes[1], gender, emotion, user_face=user_face, mode="head"
    )
    scene_clips.append(VideoFileClip(scene2).fx(vfx.fadein, 0.4).fx(vfx.zoom_in, 1.1))

    # Scene 3: REEL + BROLL MIX
    scene3 = generate_talking_avatar(
        scenes[2], gender, emotion, user_face=user_face, mode="reel"
    )
    scene_clips.append(VideoFileClip(scene3).fx(vfx.fadein, 0.4))

    # Join all scenes
    final = concatenate_videoclips(scene_clips, method="compose")

    out_id = str(uuid.uuid4())[:8]
    out_path = f"static/videos/multiscene_{out_id}.mp4"

    final.write_videofile(
        out_path,
        fps=24,
        codec="libx264",
        audio_codec="aac"
    )

    return out_path
