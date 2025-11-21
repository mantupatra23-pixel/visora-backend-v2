import uuid
import os
from moviepy.editor import VideoFileClip, CompositeVideoClip, TextClip, ImageClip, AudioFileClip, vfx

def mix_avatar_with_template(avatar_video, bg_template, script_text):
    # Load clips
    avatar = VideoFileClip(avatar_video)
    bg = VideoFileClip(bg_template).subclip(0, avatar.duration)

    # Resize background to match avatar
    bg = bg.resize(width=1080).fx(vfx.colorx, 1.2)

    # Blur + depth feel
    bg = bg.fx(vfx.blur, 2)

    # Avatar foreground slight zoom
    avatar = avatar.resize(height=1600).set_position(("center", "center"))
    avatar = avatar.fx(vfx.lum_contrast, 10, 30, 180)

    # Subtitles
    subtitle = TextClip(
        script_text,
        fontsize=70,
        color="white",
        stroke_color="black",
        stroke_width=3,
        method="caption",
        size=(900, None)
    ).set_position(("center", "bottom")).set_duration(avatar.duration)

    # Combine
    final = CompositeVideoClip([bg, avatar, subtitle])

    # Add audio back
    if avatar.audio:
        final = final.set_audio(avatar.audio)

    # Save
    out_id = str(uuid.uuid4())[:8]
    output_path = f"static/videos/mixed_{out_id}.mp4"

    final.write_videofile(
        output_path,
        fps=24,
        codec="libx264",
        audio_codec="aac"
    )

    return output_path
